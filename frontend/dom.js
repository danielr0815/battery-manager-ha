import { localize } from "./translations.js";

// Replacing the shadow tree must not reset the user's reading state on each
// HA publication. Restore before layout/paint, including nested scroll hosts.
export function replaceCardHTML(card, html) {
  const root = card.shadowRoot;
  const key = (node) => node.dataset.viewKey || node.id;
  const details = [...root.querySelectorAll("details[data-view-key]")];
  const opened = new Map(details.map((node) => [key(node), node.open]));
  const focused = root.activeElement;
  const focusKey =
    focused?.tagName === "SUMMARY" ? key(focused.parentElement) : null;
  const controlKey = focused?.dataset?.focusKey;
  const selection =
    controlKey && typeof focused.selectionStart === "number"
      ? [focused.selectionStart, focused.selectionEnd]
      : null;
  const scrolls = [];
  for (
    let node = card;
    node;
    node = node.assignedSlot || node.parentNode || node.host
  ) {
    if (typeof node.scrollTop === "number")
      scrolls.push([node, node.scrollTop, node.scrollLeft]);
  }
  const localScrolls = new Map(
    [...root.querySelectorAll("[data-scroll-key]")].map((node) => [
      node.dataset.scrollKey,
      [node.scrollTop, node.scrollLeft],
    ]),
  );
  // Anchor the visible report/day when an earlier day's height changes.
  const candidates = [
    ...root.querySelectorAll("[data-view-key], [data-scroll-key]"),
  ];
  const visible = candidates.filter((node) => {
    const rect = node.getBoundingClientRect?.();
    return (
      rect && rect.bottom > 0 && rect.top < (window.innerHeight || Infinity)
    );
  });
  const anchor =
    visible.find((node) => node.getBoundingClientRect().top >= 0) ||
    visible.at(-1);
  const anchorKey =
    anchor && (anchor.dataset.viewKey || anchor.dataset.scrollKey);
  const anchorTop = anchor?.getBoundingClientRect().top;
  // A fresh ha-card has no shadow content until Lit's asynchronous update.
  // Measuring/restoring scroll in that gap collapses the dashboard to the top.
  // Reuse the rendered frame so its slot keeps the new content in layout.
  const frame = root.querySelector?.("ha-card");
  if (frame) {
    const template = card.ownerDocument.createElement("template");
    template.innerHTML = html;
    const nextFrame = template.content.querySelector("ha-card");
    for (const attr of [...frame.attributes]) frame.removeAttribute(attr.name);
    for (const attr of nextFrame.attributes)
      frame.setAttribute(attr.name, attr.value);
    frame.replaceChildren(...nextFrame.childNodes);
    nextFrame.replaceWith(frame);
    root.replaceChildren(template.content);
  } else {
    root.innerHTML = html;
  }
  for (const node of root.querySelectorAll("details[data-view-key]")) {
    if (opened.has(key(node))) node.open = opened.get(key(node));
    if (key(node) === focusKey)
      node.querySelector("summary")?.focus({ preventScroll: true });
  }
  if (controlKey) {
    const control = [...root.querySelectorAll("[data-focus-key]")].find(
      (node) => node.dataset.focusKey === controlKey,
    );
    control?.focus({ preventScroll: true });
    if (selection && control?.setSelectionRange)
      control.setSelectionRange(...selection);
  }
  for (const node of root.querySelectorAll("[data-scroll-key]")) {
    const position = localScrolls.get(node.dataset.scrollKey);
    if (position) [node.scrollTop, node.scrollLeft] = position;
  }
  // Restore before measuring: browser-clamped offsets are not content growth.
  for (const [node, top, left] of scrolls) {
    if (node.scrollTop !== top) node.scrollTop = top;
    if (node.scrollLeft !== left) node.scrollLeft = left;
  }
  if (anchorKey) {
    const replacement = [
      ...root.querySelectorAll("[data-view-key], [data-scroll-key]"),
    ].find(
      (node) => (node.dataset.viewKey || node.dataset.scrollKey) === anchorKey,
    );
    const delta = replacement
      ? replacement.getBoundingClientRect().top - anchorTop
      : 0;
    const host = scrolls.find(
      ([node]) => node.scrollHeight > node.clientHeight,
    );
    if (host) host[0].scrollTop += delta;
  }
}

export function restoreChartSelection(card) {
  if (card._selectedTime == null) return;
  const index =
    card._chartMeta?.points.findIndex((p) => p.time === card._selectedTime) ??
    -1;
  if (index >= 0) card._showSlot(index);
  else {
    card._selectedTime = null;
    const readout = card.shadowRoot.getElementById("readout");
    if (readout)
      readout.textContent = localize(card._hass, "selection_removed");
  }
}

export function bindEntityButtons(card) {
  for (const button of card.shadowRoot.querySelectorAll("[data-entity-id]")) {
    button.addEventListener("click", () =>
      card.dispatchEvent(
        new CustomEvent("hass-more-info", {
          detail: { entityId: button.dataset.entityId },
          bubbles: true,
          composed: true,
        }),
      ),
    );
  }
}
