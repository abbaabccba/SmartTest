export function createTools({ root }) {
  root.innerHTML = `<section class="report-workspace tools-workspace">
    <header class="report-page-head"><div><h1>Tools</h1></div></header>
  </section>`
  return {
    async start() {},
    destroy() {},
  }
}
