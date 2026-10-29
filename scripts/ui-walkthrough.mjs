// Fund Desk UI walkthrough: one request through all six stages in a visible Chrome window,
// each role in its own window, with a caption for every step.
//
//   cd scripts && npm install          (once: puppeteer-core, uses your installed Chrome)
//   node ui-walkthrough.mjs                                   # web app at http://localhost:5173
//   node ui-walkthrough.mjs --ui http://localhost:5173 --pause 2500 --chrome "C:\path\chrome.exe"
//
// Needs the API and the web app running on a seeded database. Three demo users sign in once each.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import puppeteer from 'puppeteer-core'

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`)
  return i > 0 ? process.argv[i + 1] : fallback
}
const UI = arg('ui', 'http://localhost:5173').replace(/\/$/, '')
const PAUSE = Number(arg('pause', '1800'))
const CHROME = arg('chrome', process.platform === 'win32'
  ? 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
  : '/usr/bin/google-chrome')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// ---- sample receipts ------------------------------------------------------------------------
const FILES = fs.mkdtempSync(path.join(os.tmpdir(), 'funddesk-'))
const PDF = Buffer.from('%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n')
const PNG = Buffer.from('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a4c50000000049454e44ae426082', 'hex')
for (const [name, data] of [['bus-tickets.pdf', PDF], ['hotel-receipt.png', PNG], ['hotel-itemised-bill.pdf', PDF]]) {
  fs.writeFileSync(path.join(FILES, name), data)
}
const now = new Date()
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const TODAY = `${String(now.getDate()).padStart(2, '0')} ${MON[now.getMonth()]} ${now.getFullYear()}`

// ---- browser and one window per role ----------------------------------------------------------
const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: false,
  defaultViewport: { width: 1440, height: 800 },
  args: ['--no-first-run', '--disable-extensions', '--window-size=1460,900'],
})
const TOTAL = 13
let stepNo = 0

async function role(email, label) {
  const ctx = await browser.createBrowserContext()
  const page = await ctx.newPage()
  const S = { email, label, ctx, page }
  page.on('pageerror', (e) => console.log(`  [${label}] page error: ${e.message}`))
  await S_go(S, '/login')
  await page.type('#email', email, { delay: 25 })
  await page.type('#password', 'Demo@1234', { delay: 25 })
  await page.click('button[type=submit]')
  await page.waitForSelector('.sidebar', { timeout: 60000 })
  return S
}

async function caption(S, text) {
  await S.page.evaluate((t) => {
    let el = document.getElementById('walk-caption')
    if (!el) {
      el = document.createElement('div')
      el.id = 'walk-caption'
      el.style.cssText = 'position:fixed;right:20px;bottom:20px;z-index:99999;max-width:560px;padding:12px 16px;border-radius:8px;background:#17202b;color:#fff;font:600 14px "Segoe UI",Arial,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.25)'
      document.body.appendChild(el)
    }
    el.textContent = t
  }, text)
}

async function step(S, text, fn) {
  stepNo += 1
  const line = `Step ${stepNo}/${TOTAL} · ${S.label}: ${text}`
  console.log(line)
  await S.page.bringToFront()
  await caption(S, line)
  await sleep(PAUSE / 2)
  await fn()
  await caption(S, `${line}  ✓`)
  await sleep(PAUSE)
}

// ---- helpers (selectors match the Fund Desk UI) ------------------------------------------------
const idle = (S) => S.page.waitForFunction(() => !document.querySelector('.ant-skeleton, .ant-spin-spinning'), { timeout: 15000, polling: 200 }).catch(() => {})
async function S_go(S, p) {
  await S.page.goto(UI + p, { waitUntil: 'load', timeout: 120000 })
  await S.page.waitForSelector('.page, .auth', { timeout: 120000 })
  await idle(S)
}
const visibleModal = () => [...document.querySelectorAll('.ant-modal-wrap')].filter((w) => getComputedStyle(w).display !== 'none').pop()
async function click(S, text, scope = 'body') {
  const ok = await S.page.evaluate((sc, t, vm) => {
    const root = sc === 'modal' ? new Function(`return (${vm})()`)() : document.querySelector(sc)
    const el = root && [...root.querySelectorAll('button')].find((b) => b.textContent.trim().startsWith(t) && !b.disabled)
    if (el) el.click()
    return Boolean(el)
  }, scope, text, visibleModal.toString())
  if (!ok) throw new Error(`No enabled button "${text}" in ${scope}`)
  await sleep(500)
}
async function rowButton(S, rowText, btnText) {
  const ok = await S.page.evaluate((r, t) => {
    const row = [...document.querySelectorAll('.ant-table-tbody tr')].find((tr) => tr.textContent.includes(r))
    const el = row && [...row.querySelectorAll('button')].find((b) => b.textContent.trim().startsWith(t) && !b.disabled)
    if (el) el.click()
    return Boolean(el)
  }, rowText, btnText)
  if (!ok) throw new Error(`No enabled "${btnText}" in row "${rowText}"`)
  await sleep(500)
}
const waitModal = (S) => S.page.waitForFunction(() => [...document.querySelectorAll('.ant-modal-wrap')].some((w) => getComputedStyle(w).display !== 'none' && w.getBoundingClientRect().width > 0), { timeout: 15000, polling: 200 }).then(() => sleep(600))
const modalClosed = (S) => S.page.waitForFunction(() => ![...document.querySelectorAll('.ant-modal-wrap')].some((w) => getComputedStyle(w).display !== 'none'), { timeout: 15000, polling: 200 }).then(() => sleep(300))
async function typeInModal(S, selector, text) {
  const handle = await S.page.evaluateHandle((sel) => [...document.querySelectorAll('.ant-modal-wrap')].filter((w) => getComputedStyle(w).display !== 'none').pop().querySelector(sel), selector)
  await handle.asElement().type(text, { delay: 20 })
}
async function waitStatus(S, label) {
  await S.page.waitForFunction((l) => document.querySelector('.page-title')?.textContent.includes(l), { timeout: 20000, polling: 200 }, label)
  await idle(S)
}
async function pickSelect(S, id, text) {
  await S.page.click(`#${id}`)
  await S.page.type(`#${id}`, text, { delay: 25 })
  await sleep(600)
  await S.page.keyboard.press('Enter')
  await sleep(300)
}
async function clearAndType(S, selector, text) {
  await S.page.click(selector)
  await S.page.keyboard.down('Control'); await S.page.keyboard.press('KeyA'); await S.page.keyboard.up('Control')
  await S.page.keyboard.press('Backspace')
  await S.page.type(selector, text, { delay: 20 })
}
const scrollTo = (S, text) => S.page.evaluate((t) => {
  const el = [...document.querySelectorAll('.panel-title, .panel')].find((e) => e.textContent.trim().startsWith(t))
  el?.scrollIntoView({ block: 'start', behavior: 'smooth' })
}, text).then(() => sleep(500))
const drawerOpen = (S) => S.page.waitForFunction(() => document.querySelector('.ant-drawer-open .ant-drawer-content-wrapper'), { timeout: 10000, polling: 200 }).then(() => sleep(500))
const drawerClosed = (S) => S.page.waitForFunction(() => !document.querySelector('.ant-drawer-open'), { timeout: 10000, polling: 200 }).then(() => sleep(300))
async function fillItem(S, { description, amount, category, file }) {
  await drawerOpen(S)
  const p = S.page
  await clearAndType(S, '.ant-drawer-open #description', description)
  if (amount) await clearAndType(S, '.ant-drawer-open #amount', amount)
  if (!(await p.$eval('.ant-drawer-open #expense_date', (e) => e.value))) {
    await p.click('.ant-drawer-open #expense_date')
    await p.type('.ant-drawer-open #expense_date', TODAY)
    await p.keyboard.press('Enter')
    await sleep(300)
  }
  if (category) {
    await p.click('.ant-drawer-open #category')
    await p.waitForFunction((c) => [...document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option')].some((o) => o.textContent.trim() === c), { timeout: 5000, polling: 200 }, category)
    await p.evaluate((c) => [...document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option')].find((o) => o.textContent.trim() === c).click(), category)
    await sleep(400)
  }
  if (file) {
    const before = await p.evaluate(() => document.querySelectorAll('.ant-drawer-open .drawer-files li').length)
    const input = await p.$('.ant-drawer-open input[type=file]')
    await input.uploadFile(path.join(FILES, file))
    await p.waitForFunction((n) => document.querySelectorAll('.ant-drawer-open .drawer-files li').length > n, { timeout: 15000, polling: 200 }, before)
    await sleep(400)
  }
}

// ---- the walkthrough ---------------------------------------------------------------------------
let exitCode = 0
try {
  const req = await role('req.a@demo.local', 'Requestor A')
  const appr = await role('approver.a@demo.local', 'Approver A')
  const fin = await role('finance@demo.local', 'Finance')
  let id

  await step(req, 'raises ₹30,000 on PRJ-001 for Approver A', async () => {
    await S_go(req, '/requests/new')
    await pickSelect(req, 'project_id', 'PRJ-001')
    await pickSelect(req, 'approver_id', 'Approver A')
    await req.page.type('#amount_requested', '30000', { delay: 40 })
    await req.page.type('#purpose', 'Medical team travel and stay for health camp 5', { delay: 15 })
    await sleep(800)
    await req.page.click('button[type=submit]')
    await req.page.waitForFunction(() => /\/requests\/\d+$/.test(location.pathname), { timeout: 20000, polling: 200 })
    await waitStatus(req, 'Awaiting approval')
    id = await req.page.evaluate(() => location.pathname.split('/').pop())
  })

  await step(appr, 'rejects it with a reason', async () => {
    await S_go(appr, `/requests/${id}`)
    await click(appr, 'Reject')
    await waitModal(appr)
    await typeInModal(appr, 'textarea', 'Split travel and stay into separate lines')
    await click(appr, 'Reject request', 'modal')
    await waitStatus(appr, 'Returned')
  })

  await step(req, 'sees the reason, corrects to ₹28,000 and resubmits', async () => {
    await S_go(req, `/requests/${id}`)
    await sleep(800)
    await click(req, 'Correct and resubmit')
    await req.page.waitForFunction(() => location.pathname.endsWith('/resubmit') && document.querySelector('#purpose')?.value?.length > 0, { timeout: 15000, polling: 200 })
    await clearAndType(req, '#amount_requested', '28000')
    await sleep(600)
    await req.page.click('button[type=submit]')
    await req.page.waitForFunction(() => /\/requests\/\d+$/.test(location.pathname), { timeout: 20000, polling: 200 })
    await waitStatus(req, 'Awaiting approval')
  })

  await step(appr, 'approves the corrected request', async () => {
    await S_go(appr, `/requests/${id}`)
    await click(appr, 'Approve')
    await appr.page.waitForSelector('.ant-modal-confirm', { timeout: 15000 })
    await sleep(600)
    await appr.page.evaluate(() => [...document.querySelectorAll('.ant-modal-confirm-btns button')].find((b) => b.textContent.trim() === 'Approve').click())
    await waitStatus(appr, 'Awaiting disbursement')
  })

  await step(fin, 'disburses ₹28,000 with a bank reference', async () => {
    await S_go(fin, `/requests/${id}`)
    await click(fin, 'Disburse')
    await waitModal(fin)
    await typeInModal(fin, '#txn_ref', `NEFT-${Date.now() % 1000000}`)
    await sleep(600)
    await click(fin, 'Record payment', 'modal')
    await waitStatus(fin, 'Awaiting expenses')
  })

  await step(req, 'adds two expenses with receipts (₹32,000, above the request)', async () => {
    await S_go(req, `/requests/${id}`)
    await scrollTo(req, 'Expenses')
    await click(req, 'Add expense', '.page')
    await fillItem(req, { description: 'Bus tickets, 8 people', amount: '20000', category: 'Travel', file: 'bus-tickets.pdf' })
    await click(req, 'Add item', '.ant-drawer-open')
    await drawerClosed(req)
    await click(req, 'Add expense', '.page')
    await fillItem(req, { description: 'Hotel stay, 3 nights', amount: '12000', category: 'Lodging', file: 'hotel-receipt.png' })
    await click(req, 'Add item', '.ant-drawer-open')
    await drawerClosed(req)
  })

  await step(req, 'gives the overspend justification and submits', async () => {
    await req.page.type('textarea[placeholder*="above the"]', 'Hotel rates were higher in the festival week', { delay: 15 })
    await click(req, 'Save draft', '.panel-foot')
    await req.page.waitForFunction(() => document.querySelector('.panel-foot')?.textContent.includes('All changes saved'), { timeout: 10000, polling: 200 })
    await click(req, 'Submit expenses', '.panel-foot')
    await waitModal(req)
    await click(req, 'Submit expenses', 'modal')
    await waitStatus(req, 'Expense review')
  })

  await step(appr, 'approves the bus tickets, rejects the hotel bill, sends back', async () => {
    await S_go(appr, `/requests/${id}`)
    await scrollTo(appr, 'Expenses')
    await rowButton(appr, 'Bus tickets', 'Approve')
    await appr.page.waitForFunction(() => [...document.querySelectorAll('.ant-table-tbody tr')].find((tr) => tr.textContent.includes('Bus tickets'))?.textContent.includes('Approved'), { timeout: 10000, polling: 200 })
    await rowButton(appr, 'Hotel stay', 'Reject')
    await waitModal(appr)
    await typeInModal(appr, 'textarea', 'Attach the itemised hotel bill, not the card slip')
    await click(appr, 'Reject item', 'modal')
    await modalClosed(appr)
    await sleep(800)
    await click(appr, 'Send back', '.panel-foot')
    await waitModal(appr)
    await click(appr, 'Send back', 'modal')
    await waitStatus(appr, 'Expenses returned')
  })

  await step(req, 'attaches the itemised bill (approved item stays locked) and resubmits', async () => {
    await S_go(req, `/requests/${id}`)
    await scrollTo(req, 'Expenses')
    await sleep(800)
    await rowButton(req, 'Hotel stay', 'Edit')
    await fillItem(req, { description: 'Hotel stay, 3 nights (itemised bill)', file: 'hotel-itemised-bill.pdf' })
    await click(req, 'Update item', '.ant-drawer-open')
    await drawerClosed(req)
    await click(req, 'Submit expenses', '.panel-foot')
    await waitModal(req)
    await click(req, 'Submit expenses', 'modal')
    await waitStatus(req, 'Expense review')
  })

  await step(appr, 'approves the corrected item and gives final approval', async () => {
    await S_go(appr, `/requests/${id}`)
    await scrollTo(appr, 'Expenses')
    await rowButton(appr, 'Hotel stay', 'Approve')
    await appr.page.waitForFunction(() => [...document.querySelectorAll('.panel-foot button')].some((b) => b.textContent.includes('Approve expenses')), { timeout: 10000, polling: 200 })
    await click(appr, 'Approve expenses', '.panel-foot')
    await waitModal(appr)
    await click(appr, 'Approve expenses', 'modal')
    await waitStatus(appr, 'Awaiting verification')
  })

  await step(fin, 'verifies both items', async () => {
    await S_go(fin, `/requests/${id}`)
    await scrollTo(fin, 'Expenses')
    await rowButton(fin, 'Bus tickets', 'Verify')
    await fin.page.waitForFunction(() => [...document.querySelectorAll('.ant-table-tbody tr')].find((tr) => tr.textContent.includes('Bus tickets'))?.textContent.includes('Verified'), { timeout: 10000, polling: 200 })
    await rowButton(fin, 'Hotel stay', 'Verify')
    await fin.page.waitForFunction(() => [...document.querySelectorAll('.panel-foot button')].some((b) => b.textContent.includes('Liquidate')), { timeout: 10000, polling: 200 })
  })

  await step(fin, 'liquidates: ₹32,000 verified, ₹4,000 top-up', async () => {
    await click(fin, 'Liquidate', '.panel-foot')
    await waitModal(fin)
    await sleep(PAUSE)
    await click(fin, 'Liquidate request', 'modal')
    await waitStatus(fin, 'Liquidated')
    await scrollTo(fin, 'Liquidation')
  })

  await step(fin, 'checks the project budget and its ledger', async () => {
    await S_go(fin, '/projects')
    await fin.page.evaluate(() => [...document.querySelectorAll('.ant-table-row')].find((r) => r.textContent.includes('PRJ-001'))?.click())
    await fin.page.waitForSelector('.figures', { timeout: 15000 })
    await sleep(PAUSE)
    await fin.page.evaluate(() => [...document.querySelectorAll('.ant-tabs-tab-btn')].find((t) => t.textContent.trim() === 'Ledger')?.click())
    await sleep(800)
  })

  console.log(`\nUI WALKTHROUGH: all ${TOTAL} steps completed. Request ${UI}/requests/${id}`)
  await caption(fin, `Walkthrough complete · ${TOTAL} steps · request #${id} is liquidated`)
  await sleep(PAUSE * 3)
} catch (e) {
  exitCode = 1
  console.error(`\nUI WALKTHROUGH stopped at step ${stepNo}: ${e.message}`)
  await sleep(PAUSE * 3)
}
await Promise.race([browser.close(), sleep(3000)])
process.exit(exitCode)
