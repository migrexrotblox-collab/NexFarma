(function () {
  const banner = document.getElementById("cookieConsentBanner");
  if (!banner) return;
  const storageKey = "nexafarma-cookie-consent-v1";
  const preferences = document.getElementById("cookiePreferences");
  const customize = document.getElementById("cookieCustomize");
  const acceptAll = document.getElementById("cookieAcceptAll");
  const acceptNecessary = document.getElementById("cookieAcceptNecessary");
  const save = document.getElementById("cookieSavePreferences");
  const back = document.getElementById("cookieBack");
  const analytics = document.getElementById("cookieAnalytics");
  const marketing = document.getElementById("cookieMarketing");

  function readChoice() {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || "null");
      return saved && saved.version === 1 ? saved : null;
    } catch (_) {
      return null;
    }
  }
  function close() {
    banner.hidden = true;
  }
  function storeChoice(optional) {
    const choice = {
      version: 1,
      necessary: true,
      analytics: Boolean(optional.analytics),
      marketing: Boolean(optional.marketing),
      updatedAt: new Date().toISOString()
    };
    try {
      localStorage.setItem(storageKey, JSON.stringify(choice));
    } catch (_) {
      // La preferencia se aplica en esta vista aunque el almacenamiento local no esté disponible.
    }
    close();
    window.dispatchEvent(new CustomEvent("nexafarma:cookie-consent", { detail: choice }));
  }

  if (!readChoice()) banner.hidden = false;
  acceptAll?.addEventListener("click", () => storeChoice({ analytics: true, marketing: true }));
  acceptNecessary?.addEventListener("click", () => storeChoice({ analytics: false, marketing: false }));
  customize?.addEventListener("click", () => {
    const open = preferences.hidden;
    preferences.hidden = !open;
    customize.setAttribute("aria-expanded", String(open));
    customize.hidden = open;
    acceptAll.hidden = open;
    acceptNecessary.hidden = open;
    save.hidden = !open;
    back.hidden = !open;
  });
  back?.addEventListener("click", () => {
    preferences.hidden = true;
    customize.hidden = false;
    customize.setAttribute("aria-expanded", "false");
    acceptAll.hidden = false;
    acceptNecessary.hidden = false;
    save.hidden = true;
    back.hidden = true;
  });
  save?.addEventListener("click", () => storeChoice({
    analytics: analytics.checked,
    marketing: marketing.checked
  }));
  document.getElementById("cookiePreferencesOpen")?.addEventListener("click", event => {
    event.preventDefault();
    banner.hidden = false;
    preferences.hidden = false;
    customize.hidden = true;
    customize.setAttribute("aria-expanded", "true");
    acceptAll.hidden = true;
    acceptNecessary.hidden = true;
    save.hidden = false;
    back.hidden = false;
    const saved = readChoice();
    analytics.checked = Boolean(saved?.analytics);
    marketing.checked = Boolean(saved?.marketing);
    banner.scrollIntoView({ behavior: "smooth", block: "end" });
  });
})();
