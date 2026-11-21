(() => {
  const target = new URL(document.currentScript?.dataset.target ?? "../pokedex", document.baseURI);
  target.search = location.search;
  target.hash = location.hash;
  location.replace(target.href);
})();
