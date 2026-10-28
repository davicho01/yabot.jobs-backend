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

  // A collection page (/jobs/us/<role>[/<place>|/remote][/page/<n>]): which
  // one, from its path, so these events need no re-render to carry it.
  var collection = null;
  if (pageType === "collection") {
    var match = location.pathname.match(/^\/jobs\/us\/(.+?)(?:\/page\/(\d+))?\/?$/);
    if (match) {
      collection = { collection_slug: match[1], page_number: match[2] ? Number(match[2]) : 1 };
      gtag("event", "collection_view", collection);
    }
  }
  // The spec's collection events (implementation-spec §28), by what was clicked.
  var COLLECTION_CLICKS = { collection_job: "collection_job_click", collection_link: "collection_related_click" };

  // A job page's Share (job.html.jinja): the link gets a new random share id
  // each tap, ?ref=share&sid=<id>, so a sign-up it brings is counted
  // (the app's utils/attribution). In the capture phase, so the page's own
  // share handler, which reads data-share-url, sees the new link.
  document.addEventListener("click", function (event) {
    var share = event.target.closest ? event.target.closest(".job-share[data-share-url]") : null;
    if (!share || !window.crypto || !crypto.getRandomValues) return;
    var bytes = crypto.getRandomValues(new Uint8Array(16));
    var sid = btoa(String.fromCharCode.apply(null, bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    share.setAttribute("data-share-url", share.getAttribute("data-share-url").split("?")[0] + "?ref=share&sid=" + sid);
  }, true);

  // Which links and buttons people use: only the kind (data-track), never a URL.
  // seo_click stays as it was, so its history carries on; a collection page
  // also sends its own event.
  document.addEventListener("click", function (event) {
    if (!event.target.closest) return;
    var link = event.target.closest("a[data-track], button[data-track]");
    var kind = link && link.getAttribute("data-track");
    if (link) gtag("event", "seo_click", { link_type: kind, page_type: pageType });
    if (!collection) return;
    if (link && COLLECTION_CLICKS[kind]) {
      gtag("event", COLLECTION_CLICKS[kind], collection);
      return;
    }
    var pager = event.target.closest(".pager a[rel]");
    if (pager) {
      gtag("event", "collection_pagination", Object.assign({ direction: pager.getAttribute("rel") }, collection));
    }
  });
})();
