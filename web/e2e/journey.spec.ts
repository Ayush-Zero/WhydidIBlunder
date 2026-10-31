import { expect, test, type Page } from '@playwright/test'
import positionsFixture from '../src/api/fixtures/positions.json' with { type: 'json' }
import reportFixture from '../src/api/fixtures/report.json' with { type: 'json' }
import { installMockApi, mockGameKey } from './mock-api'

const FLAGGED_PLY = 35
const flaggedPosition = positionsFixture.positions.find(
  (position) => position.ply === FLAGGED_PLY,
)!
const flaggedFen = flaggedPosition.fen_before
const bestLine = reportFixture.moves.find(
  (move) => move.ply === FLAGGED_PLY,
)!.pv_san

test.describe('browser review journey', () => {
  test('discovers, analyses, reviews and regrades a game', async ({ page }) => {
    const api = await installMockApi(page)

    await startAnalysis(page)

    await expect(
      page.getByRole('heading', { name: 'Analysing ply 1.' }),
    ).toBeVisible()
    await expect(
      page.getByRole('progressbar', { name: 'Analysis progress' }),
    ).toBeVisible()
    await expect(page.getByText('1 ply saved so far.')).toBeVisible()

    api.finishJob()

    await expect(
      page.getByRole('heading', { name: 'Analysis complete.' }),
    ).toBeVisible()
    await expect(
      page.getByRole('heading', {
        name: 'Adolf Anderssen vs Lionel Kieseritzky',
      }),
    ).toBeVisible()

    await page.getByRole('tab', { name: /Mistakes, 3 flagged/ }).click()
    const mistakes = page.getByRole('tabpanel', { name: /Mistakes/ })
    await mistakes.getByRole('button', { name: /Bd6/ }).click()

    const board = page.getByTestId('review-board')
    await expect(board).toHaveAttribute('data-fen', flaggedFen)
    await expect(page.locator('.review-move-label')).toHaveText('18. Bd6')
    await expect(board.locator('svg path[marker-end]')).toHaveCount(2)

    await page
      .getByRole('list', { name: 'Best line moves' })
      .getByRole('button', { name: bestLine[0], exact: true })
      .click()
    await expect(page.locator('.best-line-status')).toHaveText(
      `Showing best-line move 1 of ${bestLine.length}.`,
    )
    await expect(board).not.toHaveAttribute('data-fen', flaggedFen)
    await expect(board.locator('svg path[marker-end]')).toHaveCount(0)

    await page.getByRole('button', { name: 'Try again', exact: true }).click()
    await page.getByLabel('Your move').selectOption('d4')
    await page.getByRole('button', { name: 'Check move' }).click()

    const outcome = page.locator('.try-again-outcome')
    await expect(outcome).toContainText('Rejected')
    await expect(outcome).toContainText('Mistake')
    await expect(page.locator('.try-again-facts')).toContainText('d4')
    await expect(page.locator('.try-again-facts')).toContainText(
      'd4 d5 exd5',
    )
    expect(api.state.tryRequests).toEqual([
      { user_color: 'white', ply: FLAGGED_PLY, move_san: 'd4' },
    ])

    await page
      .locator('#try-again-panel')
      .getByRole('button', { name: 'Reset', exact: true })
      .click()
    await expect(
      page.getByRole('heading', { name: 'Try again' }),
    ).toBeHidden()
    await expect(board.locator('svg path[marker-end]')).toHaveCount(2)
  })

  test('cancels a running analysis and keeps the saved prefix', async ({
    page,
  }) => {
    const api = await installMockApi(page)

    await startAnalysis(page)
    await expect(
      page.getByRole('heading', { name: 'Analysing ply 1.' }),
    ).toBeVisible()

    await page.getByRole('button', { name: 'Cancel analysis' }).click()

    await expect(
      page.getByRole('heading', { name: 'The analysis was cancelled.' }),
    ).toBeVisible()
    await expect(
      page.getByRole('heading', {
        name: 'No further plies will be evaluated.',
      }),
    ).toBeVisible()
    await expect(
      page.getByText('1 ply saved before it stopped are preserved.'),
    ).toBeVisible()
    expect(api.state.cancelCalls).toBe(1)
  })

  test('reloads a durable report link', async ({ page }) => {
    await installMockApi(page)
    const query = new URLSearchParams({
      game_key: mockGameKey,
      user_color: 'white',
    })

    await page.goto(`/player/beast-ash/report?${query.toString()}`)
    await expect(
      page.getByRole('heading', {
        name: 'Adolf Anderssen vs Lionel Kieseritzky',
      }),
    ).toBeVisible()

    await page.reload()
    await expect(
      page.getByRole('heading', {
        name: 'Adolf Anderssen vs Lionel Kieseritzky',
      }),
    ).toBeVisible()
    await expect(page.getByTestId('review-board')).toHaveAttribute(
      'data-fen',
      positionsFixture.positions[0].fen_before,
    )
  })
})

async function startAnalysis(page: Page) {
  await page.goto('/')
  await page.getByLabel('Chess.com username').fill('beast-ash')
  await page.getByRole('button', { name: 'Find games' }).click()

  await expect(
    page.getByRole('heading', { name: /Games for beast-ash/i }),
  ).toBeVisible()

  const card = page
    .getByRole('article')
    .filter({
      has: page.getByRole('heading', {
        name: 'BEAST-ASH vs Gopinath_27',
      }),
    })
  await card.getByRole('button', { name: 'Analyze' }).click()

  await expect(page).toHaveURL(
    /\/player\/beast-ash\/analysis\/job-e2e$/,
  )
}
