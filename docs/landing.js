(() => {
  'use strict';

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');

  const updateNavigation = () => {
    document.body.classList.toggle('is-scrolled', window.scrollY > 24);
  };

  updateNavigation();
  window.addEventListener('scroll', updateNavigation, { passive: true });

  const showcase = document.querySelector('.app-showcase');
  const showcaseControls = [...document.querySelectorAll('[data-showcase]')];
  const showcaseScreens = [...document.querySelectorAll('[data-screen]')];

  const activateShowcase = (index) => {
    const activeIndex = (index + showcaseScreens.length) % showcaseScreens.length;

    showcaseScreens.forEach((screen, screenIndex) => {
      screen.classList.remove('is-active', 'is-prev', 'is-next');

      if (screenIndex === activeIndex) {
        screen.classList.add('is-active');
      } else if (screenIndex === (activeIndex + 1) % showcaseScreens.length) {
        screen.classList.add('is-next');
      } else {
        screen.classList.add('is-prev');
      }

      screen.setAttribute('aria-hidden', String(screenIndex !== activeIndex));
    });

    showcaseControls.forEach((control, controlIndex) => {
      const isActive = controlIndex === activeIndex;
      control.classList.toggle('is-active', isActive);
      control.setAttribute('aria-selected', String(isActive));
      control.tabIndex = isActive ? 0 : -1;
    });
  };

  showcaseControls.forEach((control, index) => {
    control.addEventListener('click', () => activateShowcase(index));
    control.addEventListener('keydown', (event) => {
      const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
      if (!keys.includes(event.key)) return;

      event.preventDefault();
      let nextIndex = index;
      if (event.key === 'ArrowLeft') nextIndex = index - 1;
      if (event.key === 'ArrowRight') nextIndex = index + 1;
      if (event.key === 'Home') nextIndex = 0;
      if (event.key === 'End') nextIndex = showcaseControls.length - 1;
      nextIndex = (nextIndex + showcaseControls.length) % showcaseControls.length;
      activateShowcase(nextIndex);
      showcaseControls[nextIndex].focus();
    });
  });

  if (showcase) {
    const phoneStack = showcase.querySelector('.phone-stack');

    showcase.addEventListener('pointermove', (event) => {
      if (reducedMotion.matches || !finePointer.matches) return;
      const bounds = showcase.getBoundingClientRect();
      const x = (event.clientX - bounds.left) / bounds.width - 0.5;
      const y = (event.clientY - bounds.top) / bounds.height - 0.5;
      phoneStack.style.setProperty('--showcase-rx', `${(-y * 3).toFixed(2)}deg`);
      phoneStack.style.setProperty('--showcase-ry', `${(x * 4).toFixed(2)}deg`);
    });

    showcase.addEventListener('pointerleave', () => {
      phoneStack.style.removeProperty('--showcase-rx');
      phoneStack.style.removeProperty('--showcase-ry');
    });
  }

  const featureButtons = [...document.querySelectorAll('.feature-card[data-image]')];
  const featureImage = document.querySelector('#feature-image');
  const featureLabel = document.querySelector('#feature-preview-label');

  const preloadImage = (source) => new Promise((resolve) => {
    const image = new Image();
    image.onload = resolve;
    image.onerror = resolve;
    image.src = source;
  });

  featureButtons.forEach((button) => {
    const image = new Image();
    image.src = button.dataset.image;
  });

  const activateFeature = async (button) => {
    if (!button || button.classList.contains('is-active')) return;

    await preloadImage(button.dataset.image);

    const updateFeature = () => {
      featureButtons.forEach((item) => {
        const isActive = item === button;
        item.classList.toggle('is-active', isActive);
        item.setAttribute('aria-selected', String(isActive));
        item.tabIndex = isActive ? 0 : -1;
      });

      featureImage.src = button.dataset.image;
      featureImage.alt = button.dataset.alt;
      featureLabel.textContent = button.querySelector('strong').textContent;
    };

    if (!reducedMotion.matches && document.startViewTransition) {
      document.startViewTransition(updateFeature);
    } else {
      updateFeature();
    }
  };

  featureButtons.forEach((button, index) => {
    button.tabIndex = button.classList.contains('is-active') ? 0 : -1;
    button.addEventListener('click', () => activateFeature(button));
    button.addEventListener('keydown', (event) => {
      const keys = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'];
      if (!keys.includes(event.key)) return;

      event.preventDefault();
      let nextIndex = index;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') nextIndex = index - 1;
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') nextIndex = index + 1;
      if (event.key === 'Home') nextIndex = 0;
      if (event.key === 'End') nextIndex = featureButtons.length - 1;
      nextIndex = (nextIndex + featureButtons.length) % featureButtons.length;
      activateFeature(featureButtons[nextIndex]);
      featureButtons[nextIndex].focus();
    });
  });

  const qrCard = document.querySelector('.qr-section');
  if (qrCard) {
    qrCard.addEventListener('pointermove', (event) => {
      if (reducedMotion.matches || !finePointer.matches) return;
      const bounds = qrCard.getBoundingClientRect();
      const x = (event.clientX - bounds.left) / bounds.width - 0.5;
      const y = (event.clientY - bounds.top) / bounds.height - 0.5;
      qrCard.style.setProperty('--qr-rotate-x', `${(-y * 7).toFixed(2)}deg`);
      qrCard.style.setProperty('--qr-rotate-y', `${(x * 8).toFixed(2)}deg`);
    });

    qrCard.addEventListener('pointerleave', () => {
      qrCard.style.removeProperty('--qr-rotate-x');
      qrCard.style.removeProperty('--qr-rotate-y');
    });
  }

})();
