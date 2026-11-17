/**
 * Google Apps Script Web App: upserts scraped rows into this spreadsheet.
 * Rows are matched on a key column (the product URL), so a daily run updates
 * prices/stock in place and appends only new products - no duplicates.
 *
 * Setup:
 *  1. Open the Google Sheet -> Extensions -> Apps Script, paste this file.
 *  2. Project Settings -> Script properties -> add SHARED_SECRET (a long random string).
 *  3. Deploy -> New deployment -> Web app (Execute as: Me, Who has access: Anyone).
 *     Put the URL in SHEETS_WEBAPP_URL and the secret in SHEETS_SHARED_SECRET.
 *
 * Request:  {"token": "...", "key": "url", "columns": [...], "rows": [{...}, ...]}
 * Response: {"status": "ok", "added": n, "updated": n} or {"status": "error", "message": "..."}
 */

var SHEET_NAME = 'Products';

function doPost(e) {
  try {
    var body = JSON.parse(e.postData.contents);
    var secret = PropertiesService.getScriptProperties().getProperty('SHARED_SECRET');
    if (!secret || body.token !== secret) return json_({ status: 'error', message: 'unauthorized' });
    var lock = LockService.getScriptLock();
    lock.waitLock(20000);
    try {
      return json_(upsert_(body.columns, body.key, body.rows || []));
    } finally {
      lock.releaseLock();
    }
  } catch (err) {
    return json_({ status: 'error', message: String(err) });
  }
}

function upsert_(columns, key, rows) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(columns);
    sheet.setFrozenRows(1);
  }
  var keyCol = columns.indexOf(key);
  var data = sheet.getDataRange().getValues();
  var rowIndexByKey = {};
  for (var i = 1; i < data.length; i++) rowIndexByKey[data[i][keyCol]] = i;

  var added = 0, updated = 0, newRows = [];
  rows.forEach(function (row) {
    var values = columns.map(function (c) { return row[c] === undefined ? '' : row[c]; });
    var existing = rowIndexByKey[row[key]];
    if (existing !== undefined) {
      data[existing] = values;
      updated++;
    } else {
      newRows.push(values);
      added++;
    }
  });
  // One batched write for updates and one for new rows keeps the script fast.
  if (data.length > 1) sheet.getRange(2, 1, data.length - 1, columns.length).setValues(data.slice(1));
  if (newRows.length) sheet.getRange(sheet.getLastRow() + 1, 1, newRows.length, columns.length).setValues(newRows);
  sheet.getRange(1, columns.length + 2).setValue('Last updated: ' + new Date().toISOString());
  return { status: 'ok', added: added, updated: updated };
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
