import { expect, test } from '@playwright/test'

test.describe('full-stack live smoke', () => {
  test.skip(
    process.env.RUN_LIVE_TESTS !== '1',
    'set RUN_LIVE_TESTS=1 to run the real API, worker and Stockfish',
  )

  test('analyses and opens a report through the real service', async ({
    page,
  }) => {
    test.setTimeout(120_000)

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
      /\/player\/beast-ash\/analysis\/[0-9a-f-]+$/,
    )
    await expect(
      page.getByRole('heading', { name: 'Analysis complete.' }),
      'the real worker should finish the four-ply fixture',
    ).toBeVisible({ timeout: 90_000 })
    await expect(
      page.getByRole('heading', { name: 'BEAST-ASH vs Gopinath_27' }),
    ).toBeVisible()
    await expect(page.getByTestId('review-board')).toHaveAttribute(
      'data-fen',
      /^rnbqkbnr/,
    )
    await expect(page.locator('.review-meta')).toContainText('Stockfish')
  })
})
