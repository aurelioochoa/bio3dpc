# Final launcher asset prompts

Mode: built-in `image_gen.imagegen`, transparent output. These are the final
prompts used for the selected assets. All three masters have been copied into
the project. The supplied Bathysphere concept is the visual reference.

## Title (`bathysphere-logo-source.png`)

The input was the generated transparent title extracted from the selected
concept. Final edit prompt:

Use case: text-localization. Edit only the small bottom subtitle on this isolated transparent logo asset: replace "BATHYSPHERE" with the exact text "BREW PC PORT" (B R E W space P C space P O R T). Keep the large BioShock 3D lettering, gold worn metal, bevels, miniature skyline, underline, horizontal flanking rules, proportions and transparent background unchanged. Match the existing subtitle's engraved gold uppercase serif style and spacing. The only words below the skyline must read BREW PC PORT. No additional text or background.

## Controls (`bathysphere-controls-source.png`)

Reference: `design-concepts/01-bathysphere.png`.

Use case: ui-mockup. Asset type: ONE production UI skin sprite atlas for the supplied Bathysphere game launcher. A SQUARE transparent-background sprite sheet in a STRICT 3-column by 3-row equally sized cell grid. Each of the nine cells contains ONLY one centered isolated control, ample transparent padding, no captions, no grid lines. Match the reference's photoreal hand-worn satin brass, warm gold bevels, dark sea-green bakelite, tiny rivets and fine engraved rims. Row 1 col 1: wide horizontal blank gold brass PLAY plaque with four inset screw heads and double beveled rim, aspect 5.6:1. Row 1 col 2: wide horizontal blank dark green secondary button with fine brass edge, aspect 4.2:1. Row 1 col 3: empty PORTRAIT preview frame aspect .78:1, thin polished brass rim and transparent hole. Row 2 col 1: empty LANDSCAPE preview frame aspect 1.32:1, matching thin brass rim and transparent hole. Row 2 col 2: long thin recessed dark slider trough with brass edges, aspect 12:1. Row 2 col 3: small rectangular vertically ridged brass slider thumb, aspect .77:1. Row 3 col 1: horizontal blank dark pill-shaped audio switch housing with thin brass edge, aspect 3.8:1. Row 3 col 2: circular brass toggle knob with bevel and diagonal engraved slot. Row 3 col 3: small circular brass radio selector bezel with glowing amber indicator centered inside. Exactly nine isolated assets in equal cells. No lettering, numbers, captions, arrows, icons, scenery or logo. This is reusable artwork for real UI controls, not a screenshot.

The returned atlas has uneven cell spacing. Crop bounds were measured against
the inspected result instead of assuming the requested equal cells.

## Icons (`bathysphere-icons-source.png`)

Reference: `design-concepts/01-bathysphere.png`.

Use case: ui-mockup. Asset type: single production small UI ICON sprite sheet matching the supplied Bathysphere launcher concept. A square transparent-background image, strict equally-sized 2 columns by 2 rows grid. Four separate centered icons, each wholly inside its cell with plenty of transparent space: top left a small eight-tooth settings gear with hollow circular center; top right a simple open file folder; bottom left a small left-pointing triangular arrow; bottom right a front-facing computer mouse outline with a small wheel. Faithful to the concept's restrained vintage aged satin brass/ivory metal icons, warm muted gold, fine subtle beveled edges, readable solid silhouettes at 20 pixels. No elaborate ornament, no background, no text, no buttons or frames. These will be cropped into individual PNG icons for actual settings, saves, scale and control hints. Keep all four icons the same visual weight.
