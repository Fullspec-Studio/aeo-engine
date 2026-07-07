# Evidence chain — one prompt, end to end

How a single buyer-intent question flows through the engine. Run 36, prompt 5, engine `perplexity:sonar` (1 of 5 samples shown). Every number in the analytics traces back to an archived raw answer like this one.

## 1. The generated buyer-intent prompt

> Which hiking boots are good for wide feet?

## 2. The raw engine answer (archived to S3 before analysis)

```text
The best hiking boots for wide feet are the **Merrell Moab 3**, **KEEN Targhee IV**, and **Altra Olympus 6 HIKE**, as these brands consistently offer anatomical fits designed for wide toes [1][2].

### Top Recommendations by Category
| Category | Best Boot | Why It Fits Wide Feet |
| :--- | :--- | :--- |
| **Definitive Top Pick** | **Merrell Moab 3 Mid GTX** | Prioritizes the anatomical needs of wide-footed hikers with consistent sizing and out-of-the-box comfort [2]. |
| **Average Day Hiker** | **KEEN Targhee IV Mid** | Arguably the best option for average hikers, featuring a wide toe box (106.4 mm) that feels unrestricted yet armored [1][4]. |
| **Zero-Drop / Wide Toe Box** | **Altra Olympus 6 HIKE** | Ideal if you prefer zero-drop footwear with a massive wide toe box (96.1 mm) for swollen or wide feet [1][4]. |
| **Wide Size Availability** | **LOWA Renegade EVO GTX** | Excellent if you size up and buy it specifically in the **wide** version [1]. |
| **Hikers with Extra Wide Feet** | **Meindl Journey GTX** | Part of Meindl’s **Comfort-Fit** range, designed specifically for those needing extra space for toe splay [3][5]. |

### Key Brand Insights
*   **U.S. Brands:** Generally off
… [truncated; full text in S3]
```

Citations returned by the engine: https://hikingfeet.com/best-hiking-boots-for-wide-feet/, https://outdooradept.com/best-hiking-boots-for-wide-feet/, https://www.thegreatoutdoorsmag.com/gear-guides/which-hiking-boots-are-best-for-wide-feet/, https://runrepeat.com/guides/best-wide-hiking-boots, https://www.reddit.com/r/hiking/comments/1hqejm6/hiking_boots_for_wide_feet/

## 3. The judged observation (LLM-as-judge, forced JSON schema, matcher-verified)

```json
{
  "engine": "perplexity",
  "model": "sonar",
  "samples_total": 5,
  "samples_present": 0,
  "rank": null,
  "sentiment": "neutral",
  "confidence_flag": "ok",
  "competitors_named": [
    "Altberg",
    "Altra",
    "Altra Lone Peak 9",
    "Altra Olympus 6 HIKE",
    "Altra Timp Hiker",
    "Asolo",
    "Columbia",
    "Columbia Newton Ridge"
  ]
}
```

The store's product was recommended in **0/5** samples for this prompt/engine — a presence *rate*, never a single-shot boolean (spec §5).

## 4. The diagnosis — why the product lost

- insufficient_data: The product lacks toe box width measurements (in mm) that winning competitors prominently feature (e.g., KEEN Targhee IV at 106.4mm, Altra Lone Peak 9 at 96.1mm)
- insufficient_data: No wide size availability is specified in the product data, while all winning competitors explicitly offer wide sizing options or are known for wide fits
- insufficient_data: Missing brand origin/manufacturing information - U.S. brands are strongly preferred for wide feet, but product data doesn't indicate where Acme is based
- insufficient_data: No comfort fit designation or anatomical fit claims that winning competitors emphasize (e.g., Meindl Comfort Fit, Hanwag wide toe boxes)
- insufficient_data: Lack of specific fit characteristics like 'roomy toe box', 'foot-shaped design', or 'zero-drop' that are critical differentiators for wide-foot shoppers
- The product description is minimal and generic ('Waterproof leather hiking boot with Vibram sole') compared to competitors who emphasize wide-foot-specific features
- Product lacks any mention of sizing recommendations or fit guidance that would help wide-footed customers
- No customer ratings or reviews data that could validate fit for wide feet
- Missing specific model features that cater to wide feet such as 'anatomical fit', 'spacious toe box', or 'wide forefoot with secure heel'
- The product title 'Acme Trail 2' provides no brand recognition in the wide-foot hiking boot market dominated by established brands like Merrell, KEEN, Altra, and Topo Athletic

Note the `insufficient_data:` prefixes: the winning competitors expose measurements the product data lacks — so the engine **refuses to invent values** and names what's missing instead.

## 5. The drafted fixes (suggestions only — never auto-applied)

- **copy** — Rewrite the product title to emphasize any wide-fit characteristics if applicable, or add 'Wide Fit Available' if the boot comes in wide sizes
- **copy** — Expand the description to include specific fit characteristics relevant to wide feet, such as toe box design, anatomical fit features, or sizing guidance for wide-footed hikers
- **schema** — Add structured data fields for: toe_box_width_mm, wide_size_available (boolean), fit_type (e.g., 'standard', 'wide', 'comfort'), heel_fit_type, and brand_origin_country
- **qa** — Measure and document the actual toe box width in millimeters to enable comparison with competitors
- **qa** — Determine if wide sizes are available or if the standard fit accommodates wide feet, and document this clearly
- **qa** — Collect customer reviews and ratings specifically addressing fit for wide feet
- **qa** — Document brand origin and manufacturing location to leverage if it's a U.S. brand (preferred for wide feet)
