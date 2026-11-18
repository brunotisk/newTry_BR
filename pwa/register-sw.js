if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js?v=5', { updateViaCache: 'none' })
      .then((registration) => registration.update())
      .catch(() => {});
  });
}
