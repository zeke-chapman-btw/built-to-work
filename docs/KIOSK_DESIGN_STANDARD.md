
## Final 1920×1080 landscape assessment layouts

- The participant experience targets a 1920×1080 landscape Windows touchscreen at normal browser zoom. The shared black header keeps the local BTW logo mathematically centered; status badges or other header items must not change its center. Preserve the red divider.
- Assessment screens fit within the viewport without page scaling, clipped content, or scrolling. Keep body and answer type readable at standing distance; use available viewport space before reducing type sizes.
- For questions without an image, place category/progress/timer metadata across the top, the large centered question below, and four equal answer cards in a 2×2 grid. Keep 16–24 px gaps and outer padding so cards are large, distinct touch targets.
- For questions with an image, keep metadata across the top, question and a large aspect-ratio-preserved `object-fit: contain` image on the left, and four equal answer cards in a 2×2 grid on the right. Question, image, and all answers remain visible together; long questions wrap without crowding the image or answers.
- The entire answer card is clickable. Preserve intentional gaps between cards, visible focus treatment, and the same answer sizing and type hierarchy in image and no-image layouts.
- The existing countdown overlay must remain legible over either question layout and must not alter assessment timing. Urgent timer styling and results/next-activity messaging retain their established behavior.
- Instructions and results stay vertically balanced with their full approved content and primary action visible. Test Mode labeling and reset/exit actions remain available without displacing the centered logo.


## Finalized reusable visual system (Build 4 polish)

- Typography has two reusable roles. Futura Condensed PT Medium handles display headings, short eyebrow/section labels, action buttons, GET READY/NEXT UP language, countdown numbers, and brief emphasis. Roboto Regular handles instructions and functional copy; Roboto Bold handles question text, answer choices, and category choices; Roboto Black is reserved for high emphasis such as totals. Keep longer copy readable and avoid applying Black to every interface element.
- Primary actions use BTW red, white text, Futura Condensed, shared rounded geometry, large touch targets, and a restrained red depth shadow with a clear pressed state. Examples include Continue, I’M READY, Start New Test, and Reset.
- Secondary actions use a black background, white Futura Condensed text, the same rounded component family, and a restrained pressed state.
- Disabled actions use neutral gray with readable white text, no active shadow, and a clear disabled cursor/state.
- Category choices stay larger than normal actions and use Roboto Bold. Unselected choices are white with a dark outline. Selected choices are BTW red with white text and a clearly visible dark #1/#2 order marker; keep the selected order readable from standing distance.
- Build for the approximately 32-inch landscape touchscreen first. Keep primary targets generously sized, aligned, and visible without scrolling at the installed kiosk resolution. Narrow layouts remain usable without reducing landscape touch sizes.
- Use a consistent vertical rhythm, align related content into a purposeful group, and preserve whitespace around the primary participant task. On Home, keep the scan task dominant and center the secondary ticket/signup footer as a balanced group.
- Future participant-facing kiosk screens must reuse these shared typography roles, button variants, category selection treatment, spacing, and status patterns instead of adding one-off styles.
- This is a purpose-built interactive exhibit/tradeshow/skills experience, not a SaaS, admin, or ordinary web-app interface.

## Instructions screen

The orientation heading is “HERE’S HOW IT WORKS.” Keep the existing three core instructions and Continue action, centered as a concise exhibit introduction. Do not add article-like layout or extra copy.
## Final 1920×1080 landscape assessment layouts

- The participant experience targets a 1920×1080 landscape Windows touchscreen at normal browser zoom. The shared black header keeps the local BTW logo mathematically centered; status badges or other header items must not change its center. Preserve the red divider.
- Assessment screens fit within the viewport without page scaling, clipped content, or scrolling. Keep body and answer type readable at standing distance; use available viewport space before reducing type sizes.
- For questions without an image, place category/progress/timer metadata across the top, the large centered question below, and four equal answer cards in a 2×2 grid. Keep 16–24 px gaps and outer padding so cards are large, distinct touch targets.
- For questions with an image, keep metadata across the top, question and a large aspect-ratio-preserved `object-fit: contain` image on the left, and four equal answer cards in a 2×2 grid on the right. Question, image, and all answers remain visible together; long questions wrap without crowding the image or answers.
- The entire answer card is clickable. Preserve intentional gaps between cards, visible focus treatment, and the same answer sizing and type hierarchy in image and no-image layouts.
- The existing countdown overlay must remain legible over either question layout and must not alter assessment timing. Urgent timer styling and results/next-activity messaging retain their established behavior.
- Instructions and results stay vertically balanced with their full approved content and primary action visible. Test Mode labeling and reset/exit actions remain available without displacing the centered logo.

## Final landscape review refinements

- Keep the question timer compact. It uses the warm yellow surface through 11 seconds and switches to the BTW red urgent state at 10 seconds or less, with readable foreground contrast.
- On image-free questions, center and constrain the answer grid so the 2-by-2 touch choices retain comfortable targets without stretching edge to edge. Keep a clear gap between the question prompt and choices.
- Keep instructions and results grouped with deliberate vertical spacing; do not add scrolling or remove approved assessment/results content.
- Development/Test Mode includes two synthetic local image examples for checking the approved image-bearing question layout. They are attached only to the Development/Test Event review question bank.
