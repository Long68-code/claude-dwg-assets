/* Glue UI cho app rà soát — dùng window.ExcelJS + window.ReviewCore */
(function () {
  var fileT = document.getElementById('fileT'), fileS = document.getElementById('fileS');
  var dropT = document.getElementById('dropT'), dropS = document.getElementById('dropS');
  var subT = document.getElementById('subT'), subS = document.getElementById('subS');
  var runBtn = document.getElementById('run'), log = document.getElementById('log');
  var result = document.getElementById('result');
  var TFILE = null, SFILE = null;

  function wire(input, drop, sub, set) {
    input.addEventListener('change', function () { if (input.files[0]) set(input.files[0], sub, drop); });
    ['dragover', 'dragenter'].forEach(function (e) {
      drop.addEventListener(e, function (ev) { ev.preventDefault(); drop.classList.add('has'); });
    });
    ['dragleave', 'drop'].forEach(function (e) {
      drop.addEventListener(e, function (ev) { ev.preventDefault(); if (e === 'dragleave') drop.classList.remove('has'); });
    });
    drop.addEventListener('drop', function (ev) {
      var f = ev.dataTransfer.files[0]; if (f) { input.files = ev.dataTransfer.files; set(f, sub, drop); }
    });
  }
  wire(fileT, dropT, subT, function (f, sub, drop) { TFILE = f; sub.textContent = f.name; drop.classList.add('has'); ready(); });
  wire(fileS, dropS, subS, function (f, sub, drop) { SFILE = f; sub.textContent = f.name; drop.classList.add('has'); ready(); });
  function ready() { runBtn.disabled = !(TFILE && SFILE); }

  function say(m) { log.textContent = m; }
  function readBuf(f) {
    return new Promise(function (res, rej) {
      var r = new FileReader();
      r.onload = function () { res(r.result); };
      r.onerror = function () { rej(new Error('Không đọc được ' + f.name)); };
      r.readAsArrayBuffer(f);
    });
  }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]; }); }

  runBtn.addEventListener('click', function () {
    runBtn.disabled = true; result.classList.add('hide');
    say('Đang đọc file…');
    setTimeout(process, 30); // nhường UI vẽ
  });

  async function process() {
    try {
      var tbuf = await readBuf(TFILE), sbuf = await readBuf(SFILE);
      say('Đang nạp workbook…');
      var twb = new ExcelJS.Workbook(); await twb.xlsx.load(tbuf);
      var swb = new ExcelJS.Workbook(); await swb.xlsx.load(sbuf);

      say('Đang đối chiếu & tô màu…');
      var flagBlank = document.getElementById('optBlank').checked;
      var res = ReviewCore.review(twb, swb, { flagBlankDesc: flagBlank });

      if (!res.scanned.length) {
        say('⚠ Không tìm thấy sheet dữ liệu có cột "Account" + "Description/Memo" trong file cần rà soát. Kiểm tra lại file.');
        runBtn.disabled = false; return;
      }

      say('Đang xuất file…');
      var out = await twb.xlsx.writeBuffer();
      var blob = new Blob([out], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
      var name = (TFILE.name.replace(/\.(xlsx|xlsm)$/i, '') || 'file') + '_reviewed.xlsx';
      var url = URL.createObjectURL(blob);

      render(res, url, name);
      // tự động tải luôn
      var a = document.createElement('a'); a.href = url; a.download = name; a.click();
      say('Xong. File đã tải xuống: ' + name);
    } catch (e) {
      say('Lỗi: ' + (e && e.message ? e.message : e));
      console.error(e);
    } finally {
      runBtn.disabled = false;
    }
  }

  function render(res, url, name) {
    document.getElementById('nRed').textContent = res.redCount;
    document.getElementById('nYel').textContent = res.yellowCount;
    document.getElementById('dlwrap').innerHTML =
      'Nếu file chưa tự tải: <a class="dl" href="' + url + '" download="' + esc(name) + '">⬇ Tải ' + esc(name) + '</a>' +
      ' &nbsp;·&nbsp; quét ' + res.scanned.map(function (s) { return esc(s.name); }).join(', ') +
      ' · dùng ' + res.rulesCount + ' quy tắc chuẩn';

    var rows = res.findings.map(function (f) {
      return '<tr class="' + (f.level === 'RED' ? 'red' : 'yel') + '">' +
        '<td class="lv">' + (f.level === 'RED' ? 'ĐỎ' : 'VÀNG') + '</td>' +
        '<td>' + esc(f.cellRef) + '</td>' +
        '<td>' + esc(f.desc) + '</td>' +
        '<td>' + esc(f.acctNow) + '</td>' +
        '<td>' + esc(f.acctSug) + '</td>' +
        '<td>' + esc(f.reason) + '</td>' +
        '<td>' + esc(f.rec) + '</td></tr>';
    }).join('');
    document.getElementById('tbl').innerHTML =
      '<thead><tr><th>Mức</th><th>Ô</th><th>Description</th><th>Account hiện tại</th>' +
      '<th>Account đề xuất</th><th>Lý do</th><th>Recommend</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="7">Không phát hiện dòng cần cảnh báo.</td></tr>') + '</tbody>';
    result.classList.remove('hide');
  }
})();
