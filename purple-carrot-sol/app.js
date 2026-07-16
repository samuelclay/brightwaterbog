const FILTER_GROUPS = [
  { key: "cuisine", label: "Cuisine", limit: 12 },
  { key: "ingredients", label: "Ingredients", limit: 14 },
  { key: "recipe_items", label: "Recipe items", limit: 14 },
];

const state = {
  query: "",
  selected: Object.fromEntries(FILTER_GROUPS.map(({ key }) => [key, new Set()])),
};

const recipeStack = document.querySelector("#recipes");
const filterGroups = document.querySelector("#filter-groups");
const visibleCount = document.querySelector("#visible-count");
const emptyState = document.querySelector("#empty-state");
const searchInput = document.querySelector("#search");
const clearButton = document.querySelector("#clear-filters");
const dialog = document.querySelector("#image-dialog");
const dialogImage = document.querySelector("#dialog-image");
const dialogCaption = document.querySelector("#dialog-caption");

let collection;
let recipes = [];
let cards = [];

const escapeHtml = (value = "") =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

const imageUrl = (path) => (path ? `./public/${path}` : null);

const formatDate = (value) => {
  if (!value) return "Unknown capture time";
  const parsed = new Date(value.replace(" ", "T"));
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(parsed);
};

function searchIndex(recipe) {
  return [
    recipe.title,
    recipe.subtitle,
    recipe.summary,
    recipe.ingredients.join(" "),
    recipe.steps.map((step) => `${step.title} ${step.text}`).join(" "),
    Object.values(recipe.tags).flat().join(" "),
  ]
    .join(" ")
    .toLocaleLowerCase();
}

function renderFilters(filters) {
  filterGroups.innerHTML = FILTER_GROUPS.map(({ key, label, limit }) => {
    const tags = filters[key] ?? [];
    const needsMore = tags.length > limit;
    return `
      <section class="filter-group" data-filter-group="${key}" data-expanded="false">
        <h2 class="filter-group__title">${label}</h2>
        <div class="filter-group__chips">
          ${tags
            .map(
              ({ tag, count }) => `
                <button class="filter-chip" type="button" data-group="${key}" data-tag="${escapeHtml(tag)}" aria-pressed="false">
                  ${escapeHtml(tag)} <span>${count}</span>
                </button>`,
            )
            .join("")}
        </div>
        ${needsMore ? `<button class="filter-group__more" type="button" data-more="${key}" aria-expanded="false">More</button>` : ""}
      </section>`;
  }).join("");

  filterGroups.addEventListener("click", (event) => {
    const chip = event.target.closest(".filter-chip");
    if (chip) {
      toggleTag(chip.dataset.group, chip.dataset.tag);
      return;
    }
    const more = event.target.closest("[data-more]");
    if (more) {
      const group = more.closest(".filter-group");
      const expanded = group.dataset.expanded !== "true";
      group.dataset.expanded = String(expanded);
      more.textContent = expanded ? "Less" : "More";
      more.setAttribute("aria-expanded", String(expanded));
    }
  });
}

function sourceFigure(recipe, metadata, label) {
  const path = imageUrl(metadata.image);
  if (!path) return "";
  const dimensions = metadata.dimensions?.join(" × ") ?? "Unknown dimensions";
  return `
    <figure class="source-page">
      <button class="source-image-button" type="button" data-full-image="${path}" data-caption="${escapeHtml(`${recipe.title} — ${label}`)}">
        <img src="${path}" loading="eager" decoding="async" alt="Original ${label.toLowerCase()} photograph for ${escapeHtml(recipe.title)}" />
      </button>
      <figcaption><span>${label}</span><span>${escapeHtml(dimensions)} px</span></figcaption>
    </figure>`;
}

function incompleteMessage(recipe) {
  if (recipe.completeness === "missing_method") {
    return "The cover was photographed, but its matching method card is not in this capture set. The original cover is preserved here without invented ingredients or steps.";
  }
  if (recipe.completeness === "missing_cover") {
    return "The complete ingredient and method card was photographed, but its matching cover is not in this capture set.";
  }
  return "";
}

function renderRecipe(recipe, index) {
  const primaryImage = imageUrl(recipe.images.cover ?? recipe.images.method);
  const sourcePages = recipe.source_metadata
    .map((metadata) => sourceFigure(
      recipe,
      metadata,
      recipe.single_page_recipe
        ? "Complete recipe card"
        : metadata.side === "method"
          ? "Ingredients & method"
          : "Cover",
    ))
    .join("");
  const allTags = [...recipe.tags.cuisine.slice(0, 2), ...recipe.tags.ingredients.slice(0, 3)];
  const nutrition = recipe.nutrition ?? {};
  const partial = incompleteMessage(recipe);

  return `
    <article class="recipe-card" data-recipe-id="${recipe.id}" style="animation-delay:${Math.min(index * 14, 180)}ms">
      <span class="binder-holes" aria-hidden="true"></span>
      <button class="recipe-poster" type="button" data-full-image="${primaryImage}" data-caption="${escapeHtml(`${recipe.title} — original photograph`)}">
        <img src="${primaryImage}" loading="eager" decoding="async" alt="Photographed Purple Carrot card for ${escapeHtml(recipe.title)}" />
      </button>
      <details>
        <summary>
          <div class="recipe-summary">
            <p class="recipe-number">Card ${String(recipe.collection_order).padStart(3, "0")}${recipe.single_page_recipe ? " · one-page recipe" : ""}</p>
            <h2>${escapeHtml(recipe.title)}</h2>
            ${recipe.subtitle ? `<p class="recipe-subtitle">${escapeHtml(recipe.subtitle)}</p>` : ""}
            <div class="recipe-tagline">${allTags.map((tag) => `<span class="mini-tag">${escapeHtml(tag)}</span>`).join("")}</div>
            <span class="recipe-open">Read recipe</span>
          </div>
        </summary>
        <div class="recipe-detail">
          <div class="recipe-copy">
            <div class="recipe-stats">
              ${recipe.servings ? `<div class="recipe-stat"><span>Serves</span><strong>${recipe.servings}</strong></div>` : ""}
              ${recipe.cook_time_minutes ? `<div class="recipe-stat"><span>Cook time</span><strong>${recipe.cook_time_minutes} min</strong></div>` : ""}
              ${nutrition.calories ? `<div class="recipe-stat"><span>Per serving</span><strong>${nutrition.calories} cal</strong></div>` : ""}
            </div>
            ${partial ? `<p class="incomplete-note">${escapeHtml(partial)}</p>` : ""}
            ${recipe.summary ? `<p class="recipe-summary-text">${escapeHtml(recipe.summary)}</p>` : ""}
            <section class="recipe-section">
              <h3>Ingredients</h3>
              ${recipe.ingredients.length ? `<ul class="ingredient-list">${recipe.ingredients.map((ingredient) => `<li>${escapeHtml(ingredient)}</li>`).join("")}</ul>` : `<p class="incomplete-note">No ingredient list was present on the photographed source page.</p>`}
            </section>
            <section class="recipe-section">
              <h3>Method</h3>
              ${recipe.steps.length ? `<div class="steps">${recipe.steps.map((step) => `
                <section class="step">
                  <span class="step__number">${step.number}</span>
                  <div><h4>${escapeHtml(step.title)}</h4><p>${escapeHtml(step.text)}</p></div>
                </section>`).join("")}</div>` : `<p class="incomplete-note">No method page was present in the photographed source set.</p>`}
            </section>
          </div>
          <aside class="source-pages">
            <h3>Original cards · tap to inspect</h3>
            ${sourcePages}
            <p class="source-meta">${recipe.source_metadata.map((metadata) => `${escapeHtml(formatDate(metadata.captured_at))} · ${escapeHtml(metadata.original_filename)} · rotation ${metadata.rotation_degrees_ccw}°`).join("<br />")}</p>
          </aside>
        </div>
      </details>
    </article>`;
}

function renderRecipes() {
  recipeStack.innerHTML = recipes.map(renderRecipe).join("");
  recipeStack.setAttribute("aria-busy", "false");
  cards = [...recipeStack.querySelectorAll(".recipe-card")];
}

function toggleTag(group, tag, force) {
  const selected = state.selected[group];
  const shouldSelect = force ?? !selected.has(tag);
  if (shouldSelect) selected.add(tag);
  else selected.delete(tag);
  const chip = filterGroups.querySelector(`[data-group="${CSS.escape(group)}"][data-tag="${CSS.escape(tag)}"]`);
  chip?.setAttribute("aria-pressed", String(shouldSelect));
  applyFilters();
}

function recipeMatches(recipe) {
  if (state.query && !recipe._searchIndex.includes(state.query)) return false;
  return FILTER_GROUPS.every(({ key }) =>
    [...state.selected[key]].every((tag) => recipe.tags[key].includes(tag)),
  );
}

function applyFilters() {
  let visible = 0;
  recipes.forEach((recipe, index) => {
    const matches = recipeMatches(recipe);
    cards[index].hidden = !matches;
    if (matches) visible += 1;
  });
  visibleCount.textContent = visible;
  emptyState.hidden = visible !== 0;
  recipeStack.hidden = visible === 0;
  const active = Boolean(state.query) || FILTER_GROUPS.some(({ key }) => state.selected[key].size);
  clearButton.disabled = !active;
}

function clearAll() {
  state.query = "";
  searchInput.value = "";
  FILTER_GROUPS.forEach(({ key }) => state.selected[key].clear());
  filterGroups.querySelectorAll(".filter-chip").forEach((chip) => chip.setAttribute("aria-pressed", "false"));
  applyFilters();
}

function openImage(path, caption) {
  dialogImage.src = path;
  dialogImage.alt = caption;
  dialogCaption.textContent = caption;
  dialog.showModal();
}

function preloadArchiveImages(items) {
  const urls = [...new Set(items.flatMap((recipe) => recipe.source_metadata.map((metadata) => imageUrl(metadata.image))))];
  return Promise.all(urls.map((url) => new Promise((resolve) => {
    const image = new Image();
    image.onload = () => resolve({ url, ok: true });
    image.onerror = () => resolve({ url, ok: false });
    image.src = url;
  }))).then((results) => ({
    requested: results.length,
    loaded: results.filter((result) => result.ok).length,
    failed: results.filter((result) => !result.ok).map((result) => result.url),
  }));
}

document.addEventListener("click", (event) => {
  const imageButton = event.target.closest("[data-full-image]");
  if (imageButton) openImage(imageButton.dataset.fullImage, imageButton.dataset.caption);
  if (event.target.closest("[data-clear-all]")) clearAll();
});

searchInput.addEventListener("input", () => {
  state.query = searchInput.value.trim().toLocaleLowerCase();
  applyFilters();
});

clearButton.addEventListener("click", clearAll);
document.querySelector("#dialog-close").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", (event) => {
  if (event.target === dialog) dialog.close();
});

document.querySelectorAll(".quick-filter").forEach((button) => {
  button.addEventListener("click", () => {
    clearAll();
    toggleTag("cuisine", button.dataset.cuisine, true);
    toggleTag("ingredients", button.dataset.ingredient, true);
    document.querySelector("#filter-title").scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

async function init() {
  try {
    const response = await fetch("./data/recipes.json");
    if (!response.ok) throw new Error(`Recipe data returned ${response.status}`);
    const data = await response.json();
    collection = data.collection;
    recipes = data.recipes.map((recipe) => ({ ...recipe, _searchIndex: searchIndex(recipe) }));
    window.archiveImagesReady = preloadArchiveImages(recipes);
    document.querySelector("#recipe-total").textContent = collection.recipe_count;
    document.querySelector("#image-total").textContent = collection.image_count;
    renderFilters(data.filters);
    renderRecipes();
    applyFilters();
  } catch (error) {
    recipeStack.innerHTML = `<section class="empty-state"><p class="eyebrow">The binder could not be opened</p><h2>Serve this folder with a local web server.</h2><p>${escapeHtml(error.message)}</p></section>`;
    recipeStack.setAttribute("aria-busy", "false");
  }
}

init();
