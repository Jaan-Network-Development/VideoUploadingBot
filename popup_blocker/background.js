// The site hijacks your first clicks to open ads, either in a new tab or by
// redirecting the current tab. Close the new tabs and go back from the redirects.
const isSite = (url) => {
  try {
    return new URL(url).hostname.endsWith("sxyprn.com");
  } catch {
    return false;
  }
};

const lastSiteUrl = new Map(); // tabId -> last sxyprn URL shown in that tab

chrome.tabs.onCreated.addListener(async (tab) => {
  if (tab.openerTabId == null) return;
  try {
    const opener = await chrome.tabs.get(tab.openerTabId);
    if (isSite(opener.url)) chrome.tabs.remove(tab.id);
  } catch {
    // opener already gone
  }
});

chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (!info.url) return;
  if (isSite(info.url)) {
    lastSiteUrl.set(tabId, info.url);
  } else if (lastSiteUrl.has(tabId) && info.url.startsWith("http")) {
    chrome.tabs.goBack(tabId).catch(() => {});
  }
});

chrome.tabs.onRemoved.addListener((tabId) => lastSiteUrl.delete(tabId));
