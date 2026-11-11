/* Protected replay bridge. Merchant UI controllers remain captured source code.
 * This file adds inert test identifiers and read-only state serialization. It
 * does not author candidate interventions or call an external merchant service.
 */
(() => {
  "use strict";
  const cfg = JSON.parse(document.getElementById("wh-replay-config").textContent);
  const reference = location.pathname === "/__reference";
  const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
    && getComputedStyle(el).visibility !== "hidden";
  const clone = obj => JSON.parse(JSON.stringify(obj));
  const panelSelectors = '[data-collection-filter],[data-collection-filter-mobile],[data-sort-group],[data-mobile-sort-group],[data-filter-group]';
  function comparisonLimits(drawer) {
    const reasons = [];
    if (document.querySelector('.fslightbox-container')?.isConnected)
      reasons.push("gallery_lightbox");
    if (document.querySelector('#product-media-container[aria-expanded="true"]'))
      reasons.push("expanded_product_gallery");
    if ([...document.querySelectorAll('#desktop-primary-navigation [data-depth="1"][aria-expanded="true"]')].some(visible))
      reasons.push("desktop_navigation_menu");
    if (visible(document.querySelector('#mobile-primary-navigation[aria-expanded="true"]')))
      reasons.push("mobile_navigation_menu");
    if (drawer && visible(document.querySelector('#discount-wrapper .discount-form')))
      reasons.push("cart_discount_form");
    if ([...document.querySelectorAll('[data-carousel] [data-movable]')].some(el =>
      visible(el) && el.style.transform && !/^translateX\(0(?:px|%)?\)$/.test(el.style.transform)))
      reasons.push("carousel_position");
    return reasons;
  }
  function scrollState() {
    const entries = [];
    document.querySelectorAll('#cart-drawer, #cart-drawer *, .gift-builder-step[id], .gift-builder-step[id] *, [data-collection-filter], [data-collection-filter] *')
      .forEach(el => {
        const style = getComputedStyle(el);
        if (!visible(el) || !/auto|scroll/.test(style.overflow + style.overflowY + style.overflowX)) return;
        let node = el, parts = [];
        while (node && node !== document.body) {
          if (node.id) { parts.unshift('#' + CSS.escape(node.id)); break; }
          const n = [...node.parentElement.children].indexOf(node) + 1;
          parts.unshift(node.tagName.toLowerCase() + ':nth-child(' + n + ')');
          node = node.parentElement;
        }
        entries.push({selector:parts.join(' > '),x:el.scrollLeft,y:el.scrollTop});
      });
    return entries;
  }
  const activeBuilder = () => {
    for (const owner of [typeof product !== "undefined" ? product : null,
                         typeof cart !== "undefined" ? cart : null]) {
      if (owner?.giftBuilder?.mainContainer?.isConnected) return owner.giftBuilder;
    }
    return null;
  };
  function tag(selector, action) {
    document.querySelectorAll(selector).forEach(el => el.dataset.whAction = action);
  }
  function annotate() {
    tag("#cart-drawer-toggle", "cart-open");
    tag("#cart-drawer-close", "cart-close");
    tag("#add-to-bag", "product-send");
    tag("#reject-non-essential", "consent-reject");
    document.querySelectorAll('a[href^="/products/"]').forEach(a => {
      const slug = a.getAttribute("href").split("/")[2];
      if (!a.closest("#cart-drawer")) a.dataset.whAction = "product:" + slug;
    });
    tag('a[href="/collections/flowers/"]', "listing");
    const copies = {};
    document.querySelectorAll("article").forEach(article => {
      const link = article.querySelector('a[href^="/products/"]');
      if (!link) return;
      const slug = link.getAttribute("href").split("/")[2];
      const index = copies[slug] || 0; copies[slug] = index + 1;
      article.querySelectorAll("h3, h2, .prices").forEach((el, i) => {
        el.dataset.whFact = "listing:" + slug + ":" + index + ":" + i;
      });
    });
    tag('.gift-builder-step[id][data-active="true"] .footer button', "gift-continue");
    tag('.gift-builder-step[id][data-active="true"] .header .action button', "gift-close");
    tag('.gift-builder-step[id][data-active="true"] .no-card-option button', "gift-no-card");
    document.querySelectorAll('[data-wh-action^="gift-"]').forEach(el => {
      if (el.closest('.gift-builder-step[id]')?.dataset.active !== "true") delete el.dataset.whAction;
    });
    document.querySelectorAll('.gift-builder-step[id] [data-variant-id]').forEach(item => {
      const step = item.closest('.gift-builder-step[id]').id;
      const variant = item.dataset.variantId;
      item.querySelectorAll('.title,.price').forEach((el, i) => {
        el.dataset.whFact = step + ':' + variant + ':' + i;
      });
    });
    document.querySelectorAll("#pdp h2, #pdp h1, #pdp .prices, #cart-drawer [data-cart-total], #cart-drawer [data-cart-subtotal]")
      .forEach((el, i) => el.dataset.whFact = "source-fact-" + i);
    const groups = typeof cart !== "undefined" && window.cart?.cartObject?.lines?.nodes
      ? cart.getBundleItems(window.cart.cartObject.lines.nodes) : [];
    document.querySelectorAll("#cart-drawer-items > div").forEach((row, index) => {
      const a = row.querySelector("[data-cart-item-title]");
      const group = groups[index];
      const main = group?.mainItem || group;
      const slug = cfg.variants[main?.merchandise?.id] || a?.getAttribute("href")?.split("/")[2];
      if (slug) {
        row.querySelectorAll("[data-cart-item-remove]").forEach(el => el.dataset.whAction = "remove:" + slug);
        row.querySelectorAll("[data-cart-item-title], [data-cart-item-price]").forEach((el, i) => el.dataset.whFact = "cart-" + slug + "-" + i);
        row.dataset.whCartItems = JSON.stringify([main, ...(group?.items || [])].filter(Boolean)
          .map(item => ({product_id:cfg.variants[item.merchandise.id],quantity:item.quantity,group_product_id:slug})));
      }
    });
  }
  // Capture mutations can create new native controls (cart rows / gift steps).
  // Observe child and state changes; avoid recursively observing our annotations.
  const observer = new MutationObserver(annotate);
  observer.observe(document.body, {childList: true, subtree: true, attributes: true,
    attributeFilter: ["data-active", "aria-expanded", "hidden"]});
  window.__webhelixReplayState = () => {
    annotate();
    const builder = activeBuilder();
    const drawer = document.querySelector("#cart-drawer")?.getAttribute("aria-expanded") === "true";
    const path = cfg.path;
    const step = document.querySelector('.gift-builder-step[id][data-active="true"]');
    const unsupported = comparisonLimits(drawer);
    const state = {
      path, product_id: path.startsWith("/products/") ? path.split("/")[2] : null,
      stage: builder ? "gift_builder" : drawer ? "cart" : path.startsWith("/products/") ? "product" : "listing",
      drawer_open: drawer, consent_open: visible(document.querySelector("#reject-non-essential")),
      busy: window.__webhelixPendingRequests > 0
        || (!!step && step.getAttribute("aria-expanded") !== "true")
        || !!step?.querySelector('.footer .text-yellow-400')
        || (step?.dataset.type === "cards" && !builder?.editor),
      cart_valid: typeof cart !== "undefined" ? cart.isValidCart().valid : false,
      cart_minimum_met: typeof cart !== "undefined" ? cart.checkMinOrderValue() : false,
      gift_step: builder?.activeStep ?? null, gift_type: step?.dataset.type ?? null,
      comparison_supported: unsupported.length === 0, unsupported_reasons: unsupported,
      gift_filter: step ? {category:step.querySelector('.categories button[data-selected]')?.dataset.category ?? null,
        only_free:step.querySelector('.only-free-filter input')?.checked ?? false} : null,
      gift_item_order: step ? [...step.querySelectorAll('.items')].map(container =>
        [...container.children].filter(el => el.hasAttribute('data-variant-id')).map(el => ({
          variant_id:el.dataset.variantId, sku:el.dataset.sku,
          selected:el.getAttribute('data-selected'),
        }))) : [],
      storage: Object.fromEntries(Object.entries(localStorage)),
      product_options: typeof product !== "undefined" ? clone(product.selectedOptions) : null,
      vase: typeof product !== "undefined" ? clone(product.vaseAddOn) : null,
      scroll: {x: scrollX, y: scrollY},
      scrollables: scrollState(),
      collection_filters: window.patchCollection ? clone(window.patchCollection.activeFilters) : null,
      collection_panels: window.patchCollection ? [...document.querySelectorAll(panelSelectors)].map(el => ({
        expanded:el.getAttribute('aria-expanded'), open:el.hasAttribute('open'),
        hidden:el.classList.contains('hidden'), invisible:el.classList.contains('invisible'),
      })) : [],
      callout_index: [...document.querySelectorAll('#callouts li')].findIndex(el => !el.classList.contains('hidden')),
      fields: [...document.querySelectorAll("input,select,textarea")].filter(e => e.id || e.name)
        .map(e => ({id:e.id, name:e.name, value:e.value, checked:e.checked, type:e.type,
          name_index:[...document.getElementsByName(e.name)].indexOf(e)})),
      rendered_cart_items: [...document.querySelectorAll("[data-wh-cart-items]")].filter(visible)
        .flatMap(el => JSON.parse(el.dataset.whCartItems)),
    };
    if (builder) {
      state.gift_snapshot = clone({mainItem: builder.mainItem, initialPayload: builder.payloadItems,
        thumbnail: builder.thumbnail, steps: builder.allSteps, activeStep: builder.activeStep,
        abortBtnText: builder.abortBtnText, saveBtnText: builder.saveBtnText});
      state.card_message = builder.cardMessage;
      state.card_message_screen = !!step?.querySelector('.group[data-card-selected="true"]');
    }
    return state;
  };
  window.__webhelixReplayReady = (async () => {
    // A reference shares only the read-only cart state, never browser storage.
    if (reference) {
      window.arena.addItemsCart = window.arena.removeItemsCart = window.arena.updateItemsCart = () => {
        throw new Error("Reference page cannot mutate the cart");
      };
    }
    if (cfg.cart_id) {
      window.cart.cartId = cfg.cart_id;
      await core.fetchCart();
    }
    if (!reference && new URL(location.href).searchParams.has("cartOpen")) {
      const deadline = Date.now() + 4000;
      while (document.querySelector("#cart-drawer")?.getAttribute("aria-expanded") !== "true") {
        if (Date.now() > deadline) throw new Error("Original cart drawer did not open");
        await new Promise(resolve => setTimeout(resolve, 50));
      }
    }
    annotate();
    return true;
  })();
  function referenceCalloutNodes() {
    if (!reference) throw new Error('Callout alternatives are only available on a read-only reference');
    const nodes = [...document.querySelectorAll('#callouts li')];
    if (nodes.length > 8) throw new Error('Too many original callout phases for a bounded comparison');
    return nodes;
  }
  window.__webhelixReferenceCallouts = () => {
    const nodes = referenceCalloutNodes();
    return {count:nodes.length, current:nodes.findIndex(el => !el.classList.contains('hidden')),
      texts:nodes.map(el => el.textContent.trim())};
  };
  window.__webhelixReferenceSetCallout = index => {
    const nodes = referenceCalloutNodes();
    if (!Number.isInteger(index) || index < 0 || index >= nodes.length)
      throw new Error('Unknown original callout phase');
    nodes.forEach((el, i) => el.classList.toggle('hidden', i !== index));
    return window.__webhelixReferenceCallouts();
  };
  window.__webhelixReplayRestore = async state => {
    if (!reference) throw new Error("Restore is only available on a read-only reference page");
    await window.__webhelixReplayReady;
    // Later captured Core subclasses can reload cartSize from the cookie as
    // string "0" after the first fetch set numeric 0. The merchant drawer uses
    // strict equality for its empty state. Refresh the read-only reference from
    // the local backend after constructors finish, before restoring/opening UI.
    // No candidate DOM, merchant asset, cart mutation or fact check is changed.
    await core.fetchCart();
    Object.entries(state.storage || {}).forEach(([key,value]) => localStorage.setItem(key,value));
    if (!state.consent_open && visible(document.querySelector("#reject-non-essential"))) core.clearModal();
    if (typeof product !== "undefined" && state.product_options) {
      product.selectedOptions = clone(state.product_options);
      product.matchVariant();
      product.vaseAddOn = state.vase;
      document.querySelectorAll("[data-add-on]").forEach(el =>
        el.toggleAttribute("data-selected", el.dataset.addOn === state.vase?.merchandiseId));
    }
    if (state.gift_snapshot) {
      const owner = typeof product !== "undefined" ? product : cart;
      owner.giftBuilder.start(clone(state.gift_snapshot), () => {});
      owner.giftBuilder.cardMessage = state.card_message;
      const deadline = Date.now() + 3000;
      while (window.__webhelixReplayState().busy) {
        if (Date.now() > deadline) throw new Error("Reference gift builder did not finish restoring");
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      if (state.card_message && owner.giftBuilder.editor)
        owner.giftBuilder.editor.root.innerHTML = state.card_message.html;
      const active = document.querySelector('.gift-builder-step[id][data-active="true"]');
      const containers = [...active.querySelectorAll('.items')];
      for (const [index, order] of (state.gift_item_order || []).entries()) {
        const container = containers[index];
        if (!container) throw new Error('Reference gift item container is missing');
        const remaining = [...container.children].filter(el => el.hasAttribute('data-variant-id'));
        if (remaining.length !== order.length) throw new Error('Reference gift item count differs');
        for (const item of order) {
          const at = remaining.findIndex(el => el.dataset.variantId === item.variant_id && el.dataset.sku === item.sku);
          if (at < 0) throw new Error('Reference gift item identity differs');
          const el = remaining.splice(at, 1)[0];
          if (item.selected === null) el.removeAttribute('data-selected');
          else el.setAttribute('data-selected', item.selected);
          container.appendChild(el);
        }
      }
      if (state.gift_filter) {
        const step = document.querySelector('.gift-builder-step[id][data-active="true"]');
        const checkbox = step.querySelector('.only-free-filter input');
        if (checkbox) checkbox.checked = state.gift_filter.only_free;
        const category = [...step.querySelectorAll('.categories button[data-category]')]
          .find(el => el.dataset.category === state.gift_filter.category);
        if (category) owner.giftBuilder.handleFilterCategory(step, {currentTarget:category});
      }
      if (state.card_message_screen) {
        const step = document.querySelector('.gift-builder-step[id][data-active="true"]');
        owner.giftBuilder.toggleCardMessageScreen(step);
        const deadline = Date.now() + 3000;
        while (!window.__webhelixReplayState().card_message_screen) {
          if (Date.now() > deadline) throw new Error("Reference card message panel did not restore");
          await new Promise(resolve => setTimeout(resolve, 50));
        }
      }
    }
    if (typeof cart !== "undefined") {
      const open = cart.container.getAttribute("aria-expanded") === "true";
      if (open !== state.drawer_open) cart.toggleCartDrawer();
    }
    for (const f of state.fields || []) {
      const el = f.id ? document.getElementById(f.id) : document.getElementsByName(f.name)[f.name_index];
      if (el) { if (el.type !== "file") el.value = f.value; el.checked = f.checked; }
    }
    if (window.patchCollection) {
      window.patchCollection.applyGridLayout();
      document.querySelectorAll('[data-filter-value]').forEach(el => {
        const group = el.closest('[data-filter-group]')?.dataset.filterGroup;
        el.checked = !!state.collection_filters?.[group]?.includes(el.dataset.filterValue);
      });
      window.patchCollection.filtersChanged();
      [...document.querySelectorAll(panelSelectors)].forEach((el, i) => {
        const panel = state.collection_panels?.[i];
        if (!panel) return;
        if (panel.expanded === null) el.removeAttribute('aria-expanded');
        else el.setAttribute('aria-expanded', panel.expanded);
        el.toggleAttribute('open', panel.open);
        el.classList.toggle('hidden', panel.hidden);
        el.classList.toggle('invisible', panel.invisible);
      });
    }
    document.querySelectorAll('#callouts li').forEach((el, i) =>
      el.classList.toggle('hidden', i !== state.callout_index));
    annotate();
    for (const scroll of state.scrollables || []) {
      const el = document.querySelector(scroll.selector);
      if (el) { el.scrollLeft = scroll.x; el.scrollTop = scroll.y; }
    }
    window.scrollTo(state.scroll?.x || 0, state.scroll?.y || 0);
    return true;
  };
})();
