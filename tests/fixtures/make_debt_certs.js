// 테스트용 가짜 부채증명서 그림을 만듭니다 (가상 자료). 실행 후 python make_debt_certs.py 로 스캔 효과를 넣습니다.
const { chromium } = require('playwright');
const path = require('path');
const css = `body{font-family:'WenQuanYi Zen Hei','Malgun Gothic',sans-serif;font-size:15px;padding:40px 60px;width:700px;background:#fff}
h1{text-align:center;letter-spacing:12px;font-size:26px} table{border-collapse:collapse;width:100%} th,td{border:1px solid #000;padding:5px}
td.n{text-align:right} p{margin:6px 0}`;
const tableCert = `<html><head><meta charset="utf-8"><style>${css}</style></head><body>
<h1>부채증명서</h1>
<p>채무자 성명 : 홍길동 &nbsp;&nbsp; 생년월일 : 1990-01-01</p>
<p>채권자 : 주식회사 국민카드</p>
<p>기준일 : 2026년 11월 20일 현재</p>
<table><tr><th>대출과목</th><th>대출일자</th><th>대출금액</th><th>원금잔액</th><th>이자</th><th>연체이자</th><th>합계</th></tr>
<tr><td>카드론</td><td>2023.07.15</td><td class=n>6,000,000</td><td class=n>5,000,000</td><td class=n>123,456</td><td class=n>45,000</td><td class=n>5,168,456</td></tr>
<tr><td>신용카드</td><td>2023.03.01</td><td class=n>-</td><td class=n>3,200,000</td><td class=n>150,000</td><td class=n>42,000</td><td class=n>3,392,000</td></tr>
<tr><td>합계</td><td></td><td></td><td class=n>8,200,000</td><td class=n>273,456</td><td class=n>87,000</td><td class=n>8,560,456</td></tr></table>
<p style="margin-top:30px">위와 같이 채무가 있음을 증명합니다.</p>
<p style="text-align:center">2026년 11월 21일</p><p style="text-align:center">주식회사 국민카드 대표이사</p>
</body></html>`;
const kvCert = `<html><head><meta charset="utf-8"><style>${css}</style></head><body>
<h1>부 채 증 명 서</h1>
<p>성 명 : 홍길동</p><p>채권자 : 에이엔대부 주식회사</p>
<p>계약일자 : 2022. 1. 10.</p><p>대출과목 : 양수채권(현대캐피탈)</p>
<p>2026. 11. 15. 현재 채무 내역</p>
<p>원 금 : 7,000,000 원</p><p>이 자 : 310,000 원</p><p>지연손해금 : 95,500 원</p><p>합 계 : 7,405,500 원</p>
<p style="margin-top:30px">위와 같이 증명합니다.</p><p style="text-align:center">에이엔대부 주식회사</p>
</body></html>`;
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({viewport:{width:820,height:900}, deviceScaleFactor: 2});
  for (const [html, name] of [[tableCert, 'cert_table_clean.png'], [kvCert, 'cert_kv_clean.png']]) {
    await p.setContent(html); await p.screenshot({ path: path.join(__dirname, name), fullPage: true });
  }
  // 전자 발급본처럼 글자가 들어 있는 PDF (OCR 없이 읽는 경로 시험용)
  await p.setContent(tableCert); await p.pdf({ path: path.join(__dirname, 'cert_table_text.pdf'), format: 'A4' });
  await b.close();
})();
