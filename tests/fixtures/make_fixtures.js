// 테스트용 가짜 신용정보조회서 PDF를 만듭니다 (실제 양식이 아닌 비슷한 구조의 가상 자료).
// 실행: node tests/fixtures/make_fixtures.js
const { chromium } = require('playwright');
const path = require('path');
const css = `body{font-family:'WenQuanYi Zen Hei','Malgun Gothic',sans-serif;font-size:10pt}
table{border-collapse:collapse;width:100%;margin-bottom:14px} th,td{border:1px solid #000;padding:3px}
h2{font-size:12pt;margin:16px 0 4px} .u{text-align:right;font-size:9pt}`;
const tableHtml = `<html><head><meta charset="utf-8"><style>${css}</style></head><body>
<h1>신용정보조회서</h1><p>성명: 홍길동(가상)   조회일: 2026-11-20</p>
<h2>1. 대출정보</h2><p class="u">(단위: 천원)</p>
<table><tr><th>번호</th><th>기관명</th><th>대출과목</th><th>대출일자</th><th>대출금액</th><th>잔액</th></tr>
<tr><td>1</td><td>주식회사 국민은행</td><td>신용대출</td><td>2022.03.15</td><td>20,000</td><td>12,345</td></tr>
<tr><td>2</td><td>현대캐피탈(주)</td><td>할부금융</td><td>2023.01.02</td><td>8,000</td><td>5,000</td></tr>
<tr><td>3</td><td>OK저축은행</td><td>신용대출</td><td>2024.06.30</td><td>3,000</td><td>2,999</td></tr></table>
<h2>2. 신용카드 정보</h2><p class="u">(단위: 천원)</p>
<table><tr><th>카드사</th><th>구분</th><th>개설일자</th><th>이용잔액</th></tr>
<tr><td>신한카드</td><td>카드론</td><td>2021.05.01</td><td>4,500</td></tr>
<tr><td>삼성카드</td><td>신용카드</td><td>2020.01.01</td><td>0</td></tr></table>
<h2>3. 연체정보</h2><p class="u">(단위: 천원)</p>
<table><tr><th>기관명</th><th>연체발생일</th><th>연체금액</th></tr>
<tr><td>현대캐피탈(주)</td><td>2025.09.01</td><td>1,200</td></tr></table>
<h2>4. 조회기록</h2>
<table><tr><th>조회기관</th><th>조회일자</th><th>조회목적</th></tr>
<tr><td>카카오뱅크</td><td>2026.10.01</td><td>대출심사</td></tr></table>
</body></html>`;
const textHtml = `<html><head><meta charset="utf-8"><style>body{font-family:'WenQuanYi Zen Hei',sans-serif;font-size:10pt} p{margin:2px 0}</style></head><body>
<p>본인신용정보 요약</p><p>대출 현황 (단위: 원)</p>
<p>우리카드 카드론 2023.04.11 3,456,789</p>
<p>에이엔대부 대부업대출 2024.02.20 1,000,000</p>
<p>국민건강보험공단 체납 2025.01.01 230,000</p>
<p>합계 4,686,789</p>
<p>조회 기록</p><p>토스뱅크 2026.11.01 대출심사</p>
</body></html>`;
(async () => {
  const b = await chromium.launch(); const p = await b.newPage();
  for (const [html, name] of [[tableHtml, 'credit_table.pdf'], [textHtml, 'credit_text.pdf']]) {
    await p.setContent(html); await p.pdf({ path: path.join(__dirname, name), format: 'A4' });
  }
  await b.close();
})();
