/* Retired Profile hashes lead to their authorized dedicated area; no data writes. */
(function () {
  'use strict';
  function followLegacy() {
    var id = window.location.hash.slice(1);
    if (!['btw-interactions','employer-interactions','relationship-followups','profile-recruiting'].includes(id)) return;
    var target = document.getElementById(id);
    var link = target && target.querySelector('a[data-profile-legacy]');
    if (!link) return;
    var url = new URL(link.href, window.location.href);
    if (url.origin === window.location.origin) window.location.replace(url.href);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', followLegacy);
  else followLegacy();
  window.addEventListener('hashchange', followLegacy);
}());
