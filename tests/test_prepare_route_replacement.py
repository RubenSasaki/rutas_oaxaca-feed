import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_route_replacement import (
    PreparationError, build_package, digest, hashes, review_capture, prepare,
)
from scripts.validate_feed import Report, validate_route, validate_new_route, load_snapshot, compare_with_base
from test_validate_feed import _route, _entry


def encode(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode()


class RouteReplacementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / 'base'
        (self.base / 'rutas').mkdir(parents=True)
        self.original = _route('RA99')
        self.original['horarios'] = [{'paradaId':'RA99_IDA_01','horariosLV':['07:00'],
                                    'horariosSab':[], 'horariosDom':[], 'servicioGratuito':False}]
        self.write(self.base/'rutas/RA99.json', self.original)
        self.other = _route('RB99')
        self.write(self.base/'rutas/RB99.json',self.other)
        self.index = {'version':'base','generatedAt':'2026-01-01T00:00:00Z',
                      'rutas':[_entry('RB99'),_entry('RA99')]}
        self.write(self.base/'rutas/indice.json',self.index)
        self.write(self.base/'eventos.json',{'version':1,'updatedAt':'2026-01-01T00:00:00Z','eventos':[]})
        self.before = hashes(self.base)
        self.proposals = self.root/'proposals'
        self.directory = self.proposals/'RA99'
        self.directory.mkdir(parents=True)
        self.new = _route('RA99')
        for key in ['nombreEN','origenEN','destinoEN']:self.new[key]=None
        self.new['paradas'][0].update(nombre='Nueva parada', lat=17.0602,nombreEN=None)
        self.new['paradas'][1]['nombreEN']=None
        self.version = 'revision-fixture-2'
        self.plan = {'schemaVersion':1,'operation':'replace-published-routes','baseCommit':'a'*40,
                     'reviewedBy':'FIXTURE','reference':'TEST NOT APPROVAL',
                     'version':'test-index','generatedAt':'2026-01-02T00:00:00Z','routes':[]}
        self.update()

    def write(self,path,obj):
        path.write_bytes(encode(obj))

    def update(self):
        approval={'approvedBy':'FIXTURE','reference':'TEST NOT PUBLISHABLE'}
        route_meta={k:v for k,v in self.new.items() if k not in {'paradas','trayecto','trayectosPorSegmento'}}
        route_meta['version']=self.version
        capture={k:self.new[k] for k in ['id','paradas','trayecto','trayectosPorSegmento']}
        layers={k:{'path':k+'.geojson','sha256':'b'*64} for k in ['idaStops','idaLine','regresoStops','regresoLine']}
        counts={'ida':sum('_IDA_' in s['id'] for s in self.new['paradas']),
                'regreso':sum('_REG_' in s['id'] for s in self.new['paradas'])}
        cp={'schemaVersion':1,'operation':'replace-published-route','routeId':'RA99', 'layers':layers,'expectedStops':counts}
        payload={'route.json':self.new,'metadata.json':{'approval':approval,'route':route_meta},
                 'capture.json':capture,'capture-plan.json':cp}
        for name,obj in payload.items():self.write(self.directory/name,obj)
        prov={'schemaVersion':1,'operation':'replace-published-route','routeId':'RA99',
              'status':'CANDIDATE_FOR_REVIEW_NOT_PUBLISHED','approval':approval,'version':self.version,
              'stopCount':len(self.new['paradas']),'expectedStops':counts,
              'sourceHashes':{k:'b'*64 for k in layers},
              'schedulePolicy':'none-phase1','relationshipPolicy':'own-route-only-no-inference'}
        for name,key in [('route.json','routeSha256'),('metadata.json','metadataSha256'),
                         ('capture.json','captureSha256'),('capture-plan.json','capturePlanSha256')]:
            prov[key]=digest((self.directory/name).read_bytes())
        self.write(self.directory/'provenance.json',prov)
        self.plan['routes']=[{'id':'RA99','version':self.version,
                             'expectedBaseSha256':self.before['rutas/RA99.json'],
                             'proposalSha256':digest((self.directory/'provenance.json').read_bytes())}]

    def build(self):
        return build_package(self.base,self.proposals,self.plan,self.root/'stage')

    def test_replacement_preserves_history_order_other_route_and_references(self):
        result=self.build()
        self.assertEqual(self.before,hashes(self.base))
        self.assertEqual(self.before,hashes(self.root/'stage/before'))
        candidate=self.root/'stage/candidate'
        self.assertEqual((self.base/'rutas/RB99.json').read_bytes(),(candidate/'rutas/RB99.json').read_bytes())
        index=json.loads((candidate/'rutas/indice.json').read_bytes())
        self.assertEqual(['RB99','RA99'],[e['id'] for e in index['rutas']])
        entry=index['rutas'][1]
        self.assertEqual(digest((candidate/'rutas/RA99.json').read_bytes()),entry['sha256'])
        self.assertNotEqual(digest((candidate/'rutas/RA99.json').read_bytes()[:-1]),entry['sha256'])
        self.assertEqual(self.original['horarios'],result['routes']['RA99']['schedulesArchivedNotTransferred'])
        self.assertTrue(result['routes']['RA99']['comparisons'][0]['reviewRequired'])
        self.assertTrue(any('horarios' in r['path'] for r in result['affectedReferences']))
        self.assertEqual([],json.loads((candidate/'rutas/RA99.json').read_bytes())['horarios'])
        self.assertTrue((self.root/'stage/READY_FOR_REVIEW').exists())

    def test_metadata_free_inspection_never_calls_it_candidate(self):
        capture={k:self.new[k] for k in ['id','paradas','trayecto','trayectosPorSegmento']}
        report=review_capture(self.base,capture)
        self.assertIn('METADATA_PENDING',report['status'])
        self.assertIsNone(report['metadataChanges'])
        self.assertEqual(self.before,hashes(self.base))

    def test_same_version_rejected_even_when_geometry_and_hash_change(self):
        self.version=self.index['rutas'][1]['version'];self.update()
        with self.assertRaisesRegex(PreparationError,'versión editorial'):self.build()
        self.assertFalse((self.root/'stage').exists())

    def test_base_or_proposal_hash_mismatch_rejected(self):
        for field in ['expectedBaseSha256','proposalSha256']:
            with self.subTest(field=field):
                self.update();self.plan['routes'][0][field]='c'*64
                with self.assertRaises(PreparationError):self.build()
        self.assertEqual(self.before,hashes(self.base))

    def test_actual_bytes_including_newline_are_bound(self):
        p=self.directory/'route.json';p.write_bytes(p.read_bytes().rstrip(b'\n'))
        with self.assertRaisesRegex(PreparationError,'bytes distintos'):self.build()

    def test_unapproved_metadata_rejected(self):
        m=json.loads((self.directory/'metadata.json').read_bytes());m['approval']['approvedBy']=None
        self.write(self.directory/'metadata.json',m)
        prov=json.loads((self.directory/'provenance.json').read_bytes())
        prov['approval']=m['approval']
        prov['metadataSha256']=digest((self.directory/'metadata.json').read_bytes())
        self.write(self.directory/'provenance.json',prov)
        self.plan['routes'][0]['proposalSha256']=digest((self.directory/'provenance.json').read_bytes())
        with self.assertRaisesRegex(PreparationError,'approvedBy'):self.build()
        self.assertFalse((self.root/'stage/READY_FOR_REVIEW').exists())

    def test_existing_route_gets_strict_ids_order_geometry_shared_links_and_schedules(self):
        mutations={
            'order':lambda r:r['paradas'].reverse(),
            'bad id':lambda r:r['paradas'][0].update(id='RA99_IDA_1'),
            'empty direction':lambda r:r['trayectosPorSegmento'].update(ida=[]),
            'inherited links':lambda r:r['paradas'][0].update(rutasQueParanAqui=['RA99','RB99']),
            'schedules':lambda r:r.update(horarios=self.original['horarios']),
        }
        for name,mutate in mutations.items():
            with self.subTest(name=name):
                route=copy.deepcopy(self.new);mutate(route)
                report=Report();validate_route(route,'RA99','test',report)
                validate_new_route(route,{**_entry('RA99'),'version':self.version},'RA99',report,replacement=True)
                self.assertTrue(report.errors)

    def test_changed_route_is_strict_even_without_explicit_tool_call(self):
        report=Report();base=load_snapshot(self.base,report)
        route=copy.deepcopy(self.original);route['paradas'].reverse();route['horarios']=[]
        self.write(self.base/'rutas/RA99.json',route)
        index=copy.deepcopy(self.index);index.update(version='new',generatedAt='2026-01-02T00:00:00Z')
        index['rutas'][1]['version']='new-2';self.write(self.base/'rutas/indice.json',index)
        current=load_snapshot(self.base,report);compare_with_base(current,base,self.base,report)
        self.assertTrue(any('IDA' in e for e in report.errors))

    def test_spanish_required_null_english_allowed_and_other_types_rejected(self):
        for name in ['nombreEN','origenEN','destinoEN']:
            good=copy.deepcopy(self.new);good[name]=None;report=Report();validate_route(good,'RA99','test',report)
            self.assertEqual([],report.errors)
            for bad in ['',False,[],17]:
                good[name]=bad;report=Report();validate_route(good,'RA99','test',report);self.assertTrue(report.errors)
        for name in ['nombre','origen','destino']:
            bad=copy.deepcopy(self.new);bad[name]=None;report=Report();validate_route(bad,'RA99','test',report);self.assertTrue(report.errors)
        bad=copy.deepcopy(self.new);bad['paradas'][0]['nombre']=None
        report=Report();validate_route(bad,'RA99','test',report);self.assertTrue(report.errors)

    def test_failure_has_no_ready_marker_and_unchanged_base(self):
        self.new['paradas'].reverse();self.update()
        with self.assertRaises(PreparationError):self.build()
        self.assertFalse((self.root/'stage/READY_FOR_REVIEW').exists())
        self.assertEqual(self.before,hashes(self.base))

    def test_version_suffix_and_global_index_change_are_required(self):
        self.version='revision-3';self.update()
        with self.assertRaisesRegex(PreparationError,'terminar en -2'):self.build()

    def test_unchanged_global_version_rejected(self):
        self.plan['version'] = self.index['version']
        with self.assertRaisesRegex(PreparationError,'nueva version global'):self.build()
        self.assertFalse((self.root/'stage/READY_FOR_REVIEW').exists())

    def test_unchanged_editorial_timestamp_rejected(self):
        self.plan['generatedAt'] = self.index['generatedAt']
        with self.assertRaisesRegex(PreparationError,'actualizar generatedAt'):self.build()

    def test_zero_or_boolean_direction_count_rejected(self):
        for invalid in [0, True]:
            self.update()
            cp=json.loads((self.directory/'capture-plan.json').read_bytes())
            cp['expectedStops']['ida']=invalid
            self.write(self.directory/'capture-plan.json',cp)
            prov=json.loads((self.directory/'provenance.json').read_bytes())
            prov['expectedStops']=cp['expectedStops']
            prov['capturePlanSha256']=digest((self.directory/'capture-plan.json').read_bytes())
            self.write(self.directory/'provenance.json',prov)
            self.plan['routes'][0]['proposalSha256']=digest((self.directory/'provenance.json').read_bytes())
            with self.assertRaisesRegex(PreparationError,'Conteos'):self.build()

    def test_inactive_index_state_is_preserved(self):
        self.index['rutas'][1]['activa']=False;self.write(self.base/'rutas/indice.json',self.index)
        self.before=hashes(self.base);self.update();self.build()
        index=json.loads((self.root/'stage/candidate/rutas/indice.json').read_bytes())
        self.assertFalse(index['rutas'][1]['activa'])

    def test_proposal_symlink_rejected(self):
        source=self.directory/'route.json'
        saved=self.root/'original-route.json'
        source.rename(saved);source.symlink_to(saved)
        with self.assertRaisesRegex(PreparationError,'enlaces'):self.build()
        self.assertFalse((self.root/'stage').exists())

    def test_output_inside_another_product_repository_rejected(self):
        app=self.root/'app';app.mkdir();(app/'.git').mkdir()
        (app/'pubspec.yaml').write_text('name: test')
        plan=self.root/'plan.json';self.write(plan,self.plan)
        with self.assertRaisesRegex(PreparationError,'repositorios de producto'):
            prepare(self.base,self.proposals,plan,app/'new-output')
        self.assertFalse((app/'new-output').exists())


if __name__=='__main__':unittest.main()
