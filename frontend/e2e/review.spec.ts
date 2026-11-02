import { expect, test } from "@playwright/test";

// Needs the seeded stack: `make seed-demo` resets angle_brackets_nda to needs_review.
test("queue -> needs_review NDA -> see the deviation -> approve", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible();
  // The queue opens on "Needs review".
  await expect(page.getByRole("tab", { name: "Needs review" })).toHaveAttribute("aria-selected", "true");

  // The row is labelled with the counterparty, the file name sits under it, and the flag names
  // the rule that failed.
  const row = page.getByRole("row", { name: /angle_brackets_nda\.pdf/ });
  await expect(row.getByText("Residuals clause")).toBeVisible();
  await row.getByRole("link").click();

  // The playbook result: the residuals rule is a deviation, and the reason is shown.
  const rule = page.getByTestId("rule-no_residuals");
  await expect(rule).toHaveText("deviation");
  await expect(page.getByTestId("rule-max_term")).toHaveText("pass");

  // The quote behind the selected field is highlighted on the PDF.
  await expect(page.getByTestId("highlight").first()).toBeVisible();

  await page.getByRole("button", { name: "Approve" }).click();

  await expect(page.getByLabel("Review").getByText("Approved", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" })).toBeDisabled();
  await expect(page.getByText("reviewer needs_review → approved").last()).toBeVisible();

  // It left the review queue.
  await page.getByRole("link", { name: "← Queue" }).click();
  await expect(page.getByRole("table").getByText("angle_brackets_nda.pdf")).toHaveCount(0);
});
