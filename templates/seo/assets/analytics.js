// Google Analytics 4 for the static SEO pages (templates/seo/_analytics.html.jinja
// loads this; static_pages.publish_assets publishes it every run, so a change here
// reaches every page without re-rendering any). Same property, consent defaults and
// settings as the frontend: yabot.jobs-frontend src/utils/analytics.ts.
(function () {
  var MEASUREMENT_ID = "G-CDDT9RZ59T";
  // EEA + UK + Switzerland: analytics cookies are off by default (GDPR/ePrivacy),
  // so those visitors are only counted by cookieless pings. Google resolves the
  // region from the visitor's IP. Keep in step with analytics.ts.
  var CONSENT_REGIONS = [
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV",
    "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO", "GB", "CH",
  ];
  var script = document.currentScript;
  var pageType = (script && script.getAttribute("data-page-type")) || "other";
  // Global Privacy Control: the visitor asked not to be tracked, wherever they are.
  var gpc = navigator.globalPrivacyControl === true;

  window.dataLayer = window.dataLayer || [];
  function gtag() {
    window.dataLayer.push(arguments);
  }
  window.gtag = gtag;
  // Nothing on these pages is ever used for ads.
  var noAds = { ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied" };
  gtag("consent", "default", Object.assign({ analytics_storage: "denied", region: CONSENT_REGIONS }, noAds));
  gtag("consent", "default", Object.assign({ analytics_storage: gpc ? "denied" : "granted" }, noAds));
  gtag("js", new Date());
  gtag("config", MEASUREMENT_ID, {
    allow_google_signals: false,
    allow_ad_personalization_signals: false,
    page_type: pageType,
  });

  var tag = document.createElement("script");
  tag.async = true;
  tag.src = "https://www.googletagmanager.com/gtag/js?id=" + MEASUREMENT_ID;
  document.head.appendChild(tag);

  // Which links people follow: only the kind of link (data-track), never its URL.
  document.addEventListener("click", function (event) {
    var link = event.target.closest ? event.target.closest("a[data-track]") : null;
    if (link) gtag("event", "seo_click", { link_type: link.getAttribute("data-track"), page_type: pageType });
  });
})();
