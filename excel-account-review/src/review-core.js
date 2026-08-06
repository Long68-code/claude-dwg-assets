/* =====================================================================
 * review-core.js  —  Logic đối chiếu Description → Account, dùng chung
 * cho cả Node (prototype) lẫn trình duyệt (app HTML).
 * Chỉ phụ thuộc vào đối tượng ExcelJS.Workbook đã được nạp sẵn.
 * ===================================================================== */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ReviewCore = factory();
})(typeof self !== 'undefined' ? self : this, function () {

  // ---- Bảng màu (ARGB, thêm alpha FF) ----
  var COLORS = {
    rowRed:    'FFFBE4E6', // hồng nhạt — cả dòng có lỗi đỏ
    rowYellow: 'FFFFF8E1', // vàng kem — cả dòng có điểm vàng
    cellRed:   'FFFFC7CE', // đỏ đậm — đúng ô sai
    cellYellow:'FFFFEB9C', // vàng đậm — đúng ô nghi ngờ
    hdr:       'FF305496', // xanh header sheet điều chỉnh
  };

  function norm(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/\s+/g, ' ').trim().toUpperCase();
  }

  // Lấy text hiển thị của một ô ExcelJS (xử lý richText / formula / object)
  function cellText(cell) {
    var v = cell ? cell.value : null;
    if (v === null || v === undefined) return '';
    if (typeof v === 'object') {
      if (Array.isArray(v.richText)) return v.richText.map(function (t) { return t.text; }).join('');
      if (v.text !== undefined && v.text !== null) return String(v.text);
      if (v.result !== undefined && v.result !== null) return String(v.result);
      if (v.hyperlink !== undefined) return String(v.text || v.hyperlink);
      if (v.formula !== undefined) return '';
      return '';
    }
    return String(v);
  }

  function solid(argb) { return { type: 'pattern', pattern: 'solid', fgColor: { argb: argb } }; }

  // ExcelJS chia sẻ 1 object style giữa nhiều ô cùng định dạng; gán .fill trực
  // tiếp sẽ làm lem màu ra mọi ô dùng chung style. Phải cấp style MỚI cho ô.
  function setFill(cell, argb) {
    cell.style = Object.assign({}, cell.style, { fill: solid(argb) });
  }

  // Chuyển một biến thể có placeholder số "N" thành regex.
  //   <...> và "..."  → .*? (bất kỳ) ; "N" đứng riêng → \d+ (một con số).
  // Nhờ vậy "<supplier ref> delivered N, returned N" khớp
  //   "16/1 99151939 delivered 3, returned 4" nhưng KHÔNG khớp biến thể
  //   dùng "received" (chữ khác nên literal "delivered" không trúng).
  function altToRegex(part) {
    var W = '';                                   // đánh dấu wildcard tạm
    var s = String(part).replace(/<[^>]*>/g, W).replace(/\.\.\.|…/g, W);
    s = s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');        // escape ký tự regex (W an toàn)
    s = s.replace(/\s+/g, '\\s+');                       // khoảng trắng linh hoạt
    s = s.replace(/\bN\b/g, '\\d+');                     // placeholder số
    s = s.replace(new RegExp(W, 'g'), '.*?');            // wildcard
    return new RegExp(s, 'i');
  }

  // ---- Tách 1 keyword/pattern thành các "biến thể" (alternatives) ----
  // Dấu " / " trong key nghĩa là HOẶC → mỗi bên là một biến thể riêng
  //   (vd. "COLES / COLESSUPERM", "BUSINESS FUEL / FLEET CARD").
  // Mỗi biến thể trả về { frags, re, score }:
  //   · Nếu có placeholder số "N": dùng regex (re), frags = null.
  //   · Còn lại: cắt theo "...", bỏ <placeholder> → mảng fragments literal
  //     (tất cả phải cùng xuất hiện trong description mới khớp).
  //   · score = độ dài đoạn literal dài nhất — dùng để chấm độ cụ thể.
  function toAlts(key) {
    var alts = [];
    String(key).split(/\s*\/\s*/).forEach(function (part) {
      var body = part.replace(/<[^>]*>/g, ' ');
      var frags = body.split(/\.\.\.|…/).map(norm).filter(function (f) { return f.length > 0; });
      if (!frags.length) return;
      var score = 0;
      frags.forEach(function (f) { score = Math.max(score, f.length); });
      if (/\bN\b/.test(body)) alts.push({ frags: null, re: altToRegex(part), score: score });
      else alts.push({ frags: frags, re: null, score: score });
    });
    return alts;
  }

  // =====================================================================
  // 1) ĐỌC FILE CHUẨN  → { rules, excluded, sheetsUsed }
  // =====================================================================
  function parseStandard(stdWb) {
    var rules = [];
    var excluded = new Set();
    var sheetsUsed = [];
    var noMapSheets = [];

    stdWb.eachSheet(function (ws) {
      // tìm dòng header trong 5 dòng đầu
      var hdrRow = -1, cols = {};
      for (var r = 1; r <= Math.min(6, ws.rowCount || 6); r++) {
        var found = {};
        for (var c = 1; c <= (ws.columnCount || 12); c++) {
          var t = norm(cellText(ws.getCell(r, c)));
          if (!t) continue;
          if (/MATCH/.test(t) && found.key === undefined) found.key = c;         // Keyword/Pattern to match
          if (t === 'ACCOUNT' && found.acct === undefined) found.acct = c;
          if (t === 'STATUS' && found.status === undefined) found.status = c;
          if (/ACCOUNT THESE APPEAR/.test(t)) found.nomap = c;                    // No Mapping Value
        }
        if (found.key !== undefined && found.acct !== undefined) { hdrRow = r; cols = found; break; }
        if (found.nomap !== undefined) { // sheet No Mapping Value
          noMapSheets.push({ ws: ws, col: found.nomap, hdr: r });
          break;
        }
      }
      if (hdrRow > 0) {
        sheetsUsed.push(ws.name);
        for (var rr = hdrRow + 1; rr <= ws.rowCount; rr++) {
          var key = cellText(ws.getCell(rr, cols.key)).trim();
          var acct = cellText(ws.getCell(rr, cols.acct)).trim();
          if (!key || !acct) continue;
          var status = cols.status ? cellText(ws.getCell(rr, cols.status)).trim() : 'CONFIRMED';
          var alts = toAlts(key);
          if (!alts.length) continue;
          rules.push({ key: key, alts: alts, acct: acct, status: status, sheet: ws.name });
        }
      }
    });

    // No Mapping Value → danh sách account bị loại
    noMapSheets.forEach(function (nm) {
      for (var r = nm.hdr + 1; r <= nm.ws.rowCount; r++) {
        var a = cellText(nm.ws.getCell(r, nm.col)).trim();
        if (!a || /^TOTAL/i.test(a)) continue;
        excluded.add(norm(a));
      }
    });

    // đánh dấu key -> nhiều account khác nhau (mơ hồ)
    var byKey = {};
    rules.forEach(function (ru) {
      var k = norm(ru.key);
      (byKey[k] = byKey[k] || new Set()).add(norm(ru.acct));
    });
    rules.forEach(function (ru) { ru.ambiguous = byKey[norm(ru.key)].size > 1; });

    return { rules: rules, excluded: excluded, sheetsUsed: sheetsUsed };
  }

  // Khớp 1 description với TẤT CẢ rule → mảng { rule, score } (score = độ cụ thể).
  function matchAll(desc, rules) {
    var dn = norm(desc);
    if (!dn) return [];
    var out = [];
    for (var i = 0; i < rules.length; i++) {
      var ru = rules[i], matched = false, score = 0;
      for (var a = 0; a < ru.alts.length; a++) {          // khớp nếu trúng BẤT KỲ biến thể nào
        var alt = ru.alts[a], ok;
        if (alt.re) {
          ok = alt.re.test(dn);
        } else {
          ok = true;
          for (var f = 0; f < alt.frags.length; f++) {
            if (dn.indexOf(alt.frags[f]) === -1) { ok = false; break; }
          }
        }
        if (ok) { matched = true; score = Math.max(score, alt.score); }
      }
      if (matched) out.push({ rule: ru, score: score });
    }
    return out;
  }

  // Rút gọn: lấy rule cụ thể nhất khớp description (cho ô Account trống).
  function matchDesc(desc, rules) {
    var all = matchAll(desc, rules);
    if (!all.length) return null;
    var max = 0; all.forEach(function (m) { if (m.score > max) max = m.score; });
    var best = all.filter(function (m) { return m.score === max; })[0].rule;
    return { rule: best };
  }

  // =====================================================================
  // 2) DÒ HEADER + CỘT trong file cần rà soát
  // =====================================================================
  function detectLayout(ws) {
    var hdrRow = -1, descCol = -1, acctCol = -1, dateCol = -1, firstCol = 1, lastCol = ws.columnCount || 1;
    for (var r = 1; r <= Math.min(15, ws.rowCount || 15); r++) {
      var dC = -1, aC = -1, dtC = -1;
      for (var c = 1; c <= (ws.columnCount || 20); c++) {
        var t = norm(cellText(ws.getCell(r, c)));
        if (!t) continue;
        if (aC === -1 && t === 'ACCOUNT') aC = c;
        if (dC === -1 && (/DESCRIPTION/.test(t) || /MEMO/.test(t))) dC = c;
        if (dtC === -1 && t === 'DATE') dtC = c;
      }
      if (dC !== -1 && aC !== -1) { hdrRow = r; descCol = dC; acctCol = aC; dateCol = dtC; break; }
    }
    // phạm vi cột dữ liệu = từ cột có header trái nhất đến phải nhất
    if (hdrRow > 0) {
      var lo = 999, hi = 0;
      for (var cc = 1; cc <= (ws.columnCount || 20); cc++) {
        if (cellText(ws.getCell(hdrRow, cc)).trim()) { lo = Math.min(lo, cc); hi = Math.max(hi, cc); }
      }
      firstCol = Math.min(lo, descCol, acctCol);
      lastCol = Math.max(hi, descCol, acctCol);
    }
    return { hdrRow: hdrRow, descCol: descCol, acctCol: acctCol, dateCol: dateCol, firstCol: firstCol, lastCol: lastCol };
  }

  // Dòng dữ liệu thật? Bỏ chân trang báo cáo & tiêu đề nhóm / subtotal:
  //   nếu có cột Date thì dòng phải có giá trị Date và KHÔNG phải ô gộp ngang
  //   (chân trang QuickBooks gộp cả dòng A:J nên ô Date là ô con của vùng gộp).
  function isDataRow(ws, r, L) {
    if (L.dateCol > 0) {
      var dc = ws.getCell(r, L.dateCol);
      if (dc.isMerged && dc.master !== dc) return false;   // ô con của vùng gộp ngang (banner/footer)
      if (dc.value === null || dc.value === undefined || dc.value === '') return false; // không có ngày → không phải giao dịch
    }
    return true;
  }

  // =====================================================================
  // 3) RÀ SOÁT + TÔ MÀU + TẠO SHEET "ĐIỀU CHỈNH"
  // =====================================================================
  function review(targetWb, stdWb, opts) {
    opts = opts || {};
    var flagBlankDesc = !!opts.flagBlankDesc;         // mặc định false
    var std = parseStandard(stdWb);
    var findings = [];    // {level, sheet, row, cellRef, desc, acctNow, acctSug, reason, rec, groupKey}
    var scanned = [];

    targetWb.eachSheet(function (ws) {
      var L = detectLayout(ws);
      if (L.hdrRow < 0 || L.acctCol < 0) return;       // không phải sheet dữ liệu
      scanned.push({ name: ws.name, hdrRow: L.hdrRow, rows: ws.rowCount, descCol: L.descCol, acctCol: L.acctCol });
      var acctLetter = ws.getColumn(L.acctCol).letter;

      for (var r = L.hdrRow + 1; r <= ws.rowCount; r++) {
        // bỏ chân trang báo cáo / tiêu đề nhóm / subtotal (không có ngày ở cột Date)
        if (!isDataRow(ws, r, L)) continue;
        var desc = cellText(ws.getCell(r, L.descCol));
        var acctCell = ws.getCell(r, L.acctCol);
        var acct = cellText(acctCell);
        // bỏ dòng cấu trúc: cả description lẫn account đều rỗng (header nhóm / subtotal / footer)
        if (!acct.trim() && !desc.trim()) continue;
        // bỏ account thuộc nhóm "No Mapping Value"
        if (std.excluded.has(norm(acct))) continue;

        var level = null, reason = '', rec = '', acctSug = '', gk = '';

        if (!acct.trim()) {
          // account trống nhưng có description
          var mB = matchDesc(desc, std.rules);
          level = 'YELLOW'; gk = 'BLANK-ACCT';
          acctSug = mB ? mB.rule.acct : '(chưa có chuẩn)';
          reason = 'Ô Account đang trống trong khi dòng có nội dung.';
          rec = mB ? ('Điền Account, gợi ý theo chuẩn: ' + mB.rule.acct + '.') : 'Điền Account phù hợp.';
        } else if (!desc.trim()) {
          if (!flagBlankDesc) continue;                // mặc định bỏ qua dòng không có key để dò
          level = 'YELLOW'; gk = 'BLANK-DESC';
          acctSug = '(chưa có chuẩn)';
          reason = 'Không có Description để đối chiếu với file chuẩn.';
          rec = 'Bổ sung mô tả hoặc kiểm tra thủ công.';
        } else {
          var all = matchAll(desc, std.rules);
          if (!all.length) {
            level = 'YELLOW'; gk = 'NO-RULE';
            acctSug = '(chưa có chuẩn)';
            reason = 'Không tìm thấy quy tắc chuẩn khớp với description này.';
            rec = 'Bổ sung quy tắc vào file chuẩn hoặc kiểm tra thủ công.';
          } else {
            // Ưu tiên: chỉ giữ các rule KHỚP CỤ THỂ NHẤT (score cao nhất).
            var maxScore = 0; all.forEach(function (mm) { if (mm.score > maxScore) maxScore = mm.score; });
            var top = all.filter(function (mm) { return mm.score === maxScore; });
            var validAccts = new Set(), topKeys = new Set();
            top.forEach(function (mm) { validAccts.add(norm(mm.rule.acct)); topKeys.add(norm(mm.rule.key)); });
            var acctN = norm(acct);
            var repKey = top[0].rule.key;

            if (validAccts.has(acctN)) {
              // Account khớp MỘT nhánh hợp lệ của quy tắc cụ thể nhất.
              var confirmedHit = top.some(function (mm) {
                return norm(mm.rule.acct) === acctN && mm.rule.status.toUpperCase() === 'CONFIRMED';
              });
              if (confirmedHit) continue;                         // ĐÚNG — không tô
              // trùng chuẩn nhưng quy tắc còn chờ duyệt -> VÀNG
              var prule = top.filter(function (mm) { return norm(mm.rule.acct) === acctN; })[0].rule;
              level = 'YELLOW'; gk = 'TOCONFIRM:' + norm(prule.key);
              acctSug = prule.acct + ' (chờ duyệt)';
              reason = 'Account trùng chuẩn tạm "' + prule.acct + '" nhưng quy tắc "' + prule.key
                     + '" đang chờ duyệt (TO CONFIRM).';
              rec = 'Hỏi Director duyệt quy tắc để chốt.';
            } else if (validAccts.size === 1) {
              // Chuẩn chỉ có 1 Account, file ghi khác -> ĐỎ (dù CONFIRMED hay chờ duyệt).
              var only = top[0].rule;
              var pend = !top.some(function (mm) { return mm.rule.status.toUpperCase() === 'CONFIRMED'; });
              level = 'RED'; gk = 'MISMATCH:' + norm(only.key);
              acctSug = only.acct + (pend ? ' (quy tắc chờ duyệt)' : '');
              reason = 'File ghi "' + acct + '", nhưng chuẩn cho "' + only.key + '" là "' + only.acct + '"'
                     + (pend ? ' (quy tắc đang chờ duyệt — TO CONFIRM).' : '.');
              rec = 'Đổi Account sang ' + only.acct + (pend ? ' (xác nhận với Director).' : '.');
            } else if (topKeys.size === 1) {
              // Cùng 1 quy tắc, nhiều nhánh Account hợp lệ, nhưng file không khớp nhánh nào -> ĐỎ.
              var choices = top.map(function (mm) { return mm.rule.acct; })
                .filter(function (v, i, a) { return a.indexOf(v) === i; }).join(' hoặc ');
              level = 'RED'; gk = 'MISMATCH:' + norm(repKey);
              acctSug = choices;
              reason = 'File ghi "' + acct + '", nhưng chuẩn cho "' + repKey + '" chỉ nhận: ' + choices + '.';
              rec = 'Đổi Account sang một trong: ' + choices + '.';
            } else {
              // Nhiều quy tắc KHÁC key, cùng độ cụ thể, trỏ Account khác nhau -> thật sự mâu thuẫn.
              level = 'YELLOW'; gk = 'AMBIGUOUS:' + norm(repKey);
              acctSug = '(chưa có chuẩn)';
              reason = 'Description khớp nhiều quy tắc cùng độ ưu tiên nhưng trỏ Account khác nhau; chuẩn chưa thống nhất.';
              rec = 'Xác nhận Account đúng theo bản chất giao dịch trước khi chốt.';
            }
          }
        }

        findings.push({
          level: level, sheet: ws.name, row: r, cellRef: acctLetter + r,
          desc: desc, acctNow: acct, acctSug: acctSug, reason: reason, rec: rec, groupKey: gk
        });
      }

      // ---- TÔ MÀU (2 tầng) ----
      var byRow = {};
      findings.filter(function (f) { return f.sheet === ws.name; }).forEach(function (f) {
        // ưu tiên đỏ nếu 1 dòng vừa đỏ vừa vàng
        if (!byRow[f.row] || (f.level === 'RED')) byRow[f.row] = f.level;
      });
      Object.keys(byRow).forEach(function (rk) {
        var r = +rk, lvl = byRow[rk];
        var rowArgb = lvl === 'RED' ? COLORS.rowRed : COLORS.rowYellow;
        for (var c = L.firstCol; c <= L.lastCol; c++) {
          var cell = ws.getCell(r, c);
          if (cell.isMerged && cell.master !== cell) continue;
          setFill(cell, rowArgb);
        }
        // tầng 2: đúng ô Account
        setFill(ws.getCell(r, L.acctCol), lvl === 'RED' ? COLORS.cellRed : COLORS.cellYellow);
      });
    });

    // ---- Sắp xếp: ĐỎ trước VÀNG, gom theo cụm lỗi ----
    var order = { 'RED': 0, 'YELLOW': 1 };
    findings.sort(function (a, b) {
      if (order[a.level] !== order[b.level]) return order[a.level] - order[b.level];
      if (a.groupKey !== b.groupKey) return a.groupKey < b.groupKey ? -1 : 1;
      if (a.sheet !== b.sheet) return a.sheet < b.sheet ? -1 : 1;
      return a.row - b.row;
    });

    var nRed = findings.filter(function (f) { return f.level === 'RED'; }).length;
    var nYel = findings.length - nRed;

    buildReviewSheet(targetWb, findings, nRed, nYel, scanned, std);

    return {
      redCount: nRed, yellowCount: nYel,
      scanned: scanned, rulesCount: std.rules.length,
      excluded: Array.from(std.excluded), findings: findings
    };
  }

  // =====================================================================
  // 4) Dựng sheet "ĐIỀU CHỈNH" và đưa lên ĐẦU
  // =====================================================================
  function buildReviewSheet(wb, findings, nRed, nYel, scanned, std) {
    var ws = wb.addWorksheet('ĐIỀU CHỈNH', { views: [{ state: 'frozen', ySplit: 7 }] });

    var scanTxt = scanned.map(function (s) {
      return s.name + ' (dòng ' + (s.hdrRow + 1) + '–' + s.rows + ')';
    }).join('; ');

    ws.getCell('A1').value = 'BẢNG ĐIỀU CHỈNH — Đối chiếu Description → Account';
    ws.getCell('A1').font = { bold: true, size: 14 };
    ws.getCell('A2').value = 'Tổng ô ĐỎ (sai rõ ràng): ' + nRed + '   |   Tổng ô VÀNG (nghi ngờ): ' + nYel;
    ws.getCell('A2').font = { bold: true };
    ws.getCell('A3').value = 'Phạm vi đã quét: ' + scanTxt;
    ws.getCell('A4').value = 'Số quy tắc chuẩn dùng: ' + std.rules.length +
      '   |   Account bỏ qua (No Mapping): ' + (Array.from(std.excluded).join(', ') || '(không)');
    ws.getCell('A5').value = 'Cách đọc màu: cả dòng tô nhạt = dòng cần để ý · ô tô đậm = đúng ô sai/nghi ngờ. '
      + 'ĐỎ = lệch trực tiếp với chuẩn CONFIRMED · VÀNG = không có chuẩn / chuẩn chờ duyệt / chuẩn mâu thuẫn / thiếu dữ liệu.';
    ws.getCell('A5').alignment = { wrapText: true };

    var headers = ['Mức độ', 'Sheet', 'Dòng', 'Ô', 'Description', 'Account hiện tại', 'Account đề xuất', 'Lý do', 'Recommend'];
    var hr = 7;
    headers.forEach(function (h, i) {
      var c = ws.getCell(hr, i + 1);
      c.value = h;
      c.font = { bold: true, color: { argb: 'FFFFFFFF' } };
      setFill(c, COLORS.hdr);
      c.alignment = { vertical: 'middle', wrapText: true };
    });

    findings.forEach(function (f, idx) {
      var r = hr + 1 + idx;
      var vals = [f.level === 'RED' ? 'ĐỎ' : 'VÀNG', f.sheet, f.row, f.cellRef, f.desc, f.acctNow, f.acctSug, f.reason, f.rec];
      vals.forEach(function (v, i) { ws.getCell(r, i + 1).value = v; });
      var tag = ws.getCell(r, 1);
      setFill(tag, f.level === 'RED' ? COLORS.cellRed : COLORS.cellYellow);
      tag.font = { bold: true };
      ws.getCell(r, 8).alignment = { wrapText: true };
      ws.getCell(r, 9).alignment = { wrapText: true };
    });

    ws.columns = [
      { width: 8 }, { width: 26 }, { width: 7 }, { width: 8 }, { width: 42 },
      { width: 22 }, { width: 24 }, { width: 50 }, { width: 34 }
    ];
    var lastRow = hr + findings.length;
    ws.autoFilter = { from: { row: hr, column: 1 }, to: { row: Math.max(hr, lastRow), column: 9 } };

    // Đưa sheet lên đầu tiên
    moveSheetFirst(wb, ws);
  }

  // Chuyển 1 worksheet lên vị trí đầu (ExcelJS không có API sẵn)
  function moveSheetFirst(wb, ws) {
    // orderNo điều khiển thứ tự tab khi ghi file
    var sheets = wb.worksheets;
    // gán lại orderNo: sheet mới = 0, các sheet còn lại dịch lên
    var others = sheets.filter(function (s) { return s.id !== ws.id; });
    ws.orderNo = 0;
    others.forEach(function (s, i) { s.orderNo = i + 1; });
  }

  return { review: review, parseStandard: parseStandard, norm: norm, cellText: cellText, detectLayout: detectLayout, COLORS: COLORS };
});
