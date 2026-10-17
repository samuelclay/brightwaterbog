// One recipe identity, with independent membership in each person's collection.
(function (root) {
  const names = {
    sam: "Sam's New York Times Cooking",
    brittany: "Brittany's New York Times Cooking",
  };
  function mergeCollections(...lists) {
    const recipes = new Map();
    lists.forEach(list => (list || []).forEach(recipe => {
      const collections = recipe.collections || ['sam'];
      const previous = recipes.get(recipe.pair);
      if (previous) {
        previous.collections = [...new Set([...previous.collections, ...collections])];
      } else {
        recipes.set(recipe.pair, {...recipe, collections: [...collections]});
      }
    }));
    return [...recipes.values()];
  }
  root.NYT_COLLECTION_NAMES = names;
  root.mergeNytCollections = mergeCollections;
  if (typeof module !== 'undefined') module.exports = {names, mergeCollections};
})(typeof window !== 'undefined' ? window : globalThis);
