import os
import base64
import json
import time
import tempfile
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path
from flask import Flask, request, jsonify, Response, redirect, session
import anthropic

app = Flask(__name__)

# Flask session cookie signing. APP_SECRET is set per-environment in Vercel.
# Falls back to a random per-process secret if missing (sessions won't survive
# cold starts, but app still loads for debugging).
app.secret_key = os.environ.get("APP_SECRET", "").strip() or secrets.token_urlsafe(32)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

INDEX_HTML = (Path(__file__).resolve().parent / "index.html").read_text()


def get_app_password():
    return os.environ.get("APP_PASSWORD", "").strip()


# Routes that bypass the auth wall: login form itself, the Vercel Blob upload
# handler (served by Node, not Flask — included here only as a safety net),
# and the debug endpoint for connectivity checks.
_AUTH_EXEMPT_PATHS = {"/login", "/api/blob-upload"}


@app.before_request
def require_auth():
    # If no password is configured, fail open so the app is still usable
    # (e.g. during first deploy before env vars land).
    if not get_app_password():
        return None
    if request.path in _AUTH_EXEMPT_PATHS:
        return None
    if session.get("authed") is True:
        return None
    # For XHR/API calls, return JSON 401 instead of redirecting so the
    # frontend can show a useful error rather than redirecting mid-fetch.
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not authenticated", "login_url": "/login"}), 401
    return redirect("/login")


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Sign in — Meta Ad Compliance Checker</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #0a0a0c; color: #eeeef5;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      min-height: 100vh; display: flex; align-items: center; justify-content: center;
    }
    .card {
      background: #141418; border: 1px solid #2a2a36; border-radius: 14px;
      padding: 36px 32px; width: 100%; max-width: 380px;
    }
    .logo {
      width: 48px; height: 48px; margin: 0 auto 20px;
      background: linear-gradient(135deg, #1877f2, #0d5bbf);
      border-radius: 12px; display: flex; align-items: center; justify-content: center;
      font-size: 22px; font-weight: 800; color: #fff;
      box-shadow: 0 2px 16px rgba(24,119,242,0.3);
    }
    h1 { font-size: 18px; font-weight: 700; text-align: center; margin-bottom: 6px; }
    .subtitle { font-size: 13px; color: #7e7e96; text-align: center; margin-bottom: 28px; }
    label { display: block; font-size: 11px; font-weight: 700; color: #7e7e96;
      text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; }
    input[type=password] {
      width: 100%; padding: 12px 14px; font-size: 14px;
      background: #1c1c22; color: #eeeef5;
      border: 1px solid #2a2a36; border-radius: 8px; outline: none;
      transition: border-color 0.15s;
    }
    input[type=password]:focus { border-color: #1877f2; }
    button {
      width: 100%; margin-top: 18px; padding: 13px;
      background: #1877f2; color: #fff; border: none; border-radius: 10px;
      font-size: 14px; font-weight: 700; cursor: pointer;
      transition: all 0.15s;
    }
    button:hover { box-shadow: 0 4px 20px rgba(24,119,242,0.25); transform: translateY(-1px); }
    .error {
      margin-top: 14px; padding: 10px 12px; background: rgba(255,59,92,0.1);
      border: 1px solid rgba(255,59,92,0.3); border-radius: 8px;
      color: #ff3b5c; font-size: 13px; text-align: center;
    }
    .footer { margin-top: 20px; font-size: 11px; color: #7e7e96; text-align: center; }
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">M</div>
    <h1>Meta Ad Compliance Checker</h1>
    <p class="subtitle">Enter the team password to continue</p>
    <form method="POST" action="/login">
      <label for="password">Password</label>
      <input type="password" id="password" name="password" autofocus autocomplete="current-password" />
      <button type="submit">Sign in</button>
      {error_block}
    </form>
    <p class="footer">Internal tool. Access restricted.</p>
  </div>
</body>
</html>
"""


@app.route("/login", methods=["GET", "POST"])
def login():
    error_block = ""
    if request.method == "POST":
        submitted = request.form.get("password", "")
        if submitted and secrets.compare_digest(submitted, get_app_password()):
            session.permanent = True
            session["authed"] = True
            next_url = request.args.get("next", "/")
            # Basic open-redirect guard: only allow same-origin paths.
            if not next_url.startswith("/") or next_url.startswith("//"):
                next_url = "/"
            return redirect(next_url)
        error_block = '<div class="error">Incorrect password</div>'
    return Response(LOGIN_HTML.replace("{error_block}", error_block), content_type="text/html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/")
def serve_index():
    return Response(INDEX_HTML, content_type="text/html")


def get_api_key():
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def get_gemini_key():
    return os.environ.get("GEMINI_API_KEY", "").strip()


@app.route("/api/debug")
def debug():
    key = get_api_key()
    has_key = bool(key)
    prefix = key[:12] + "..." if key else "(empty)"

    # Test Anthropic SDK directly
    sdk_test = "not tested"
    if has_key:
        try:
            import httpx
            http_client = httpx.Client(
                timeout=httpx.Timeout(30.0, connect=10.0),
                http1=True,
                http2=False,
            )
            c = anthropic.Anthropic(api_key=key, http_client=http_client)
            r = c.messages.create(model="claude-sonnet-4-6", max_tokens=5, messages=[{"role": "user", "content": "hi"}])
            sdk_test = f"OK: {r.content[0].text}"
        except Exception as e:
            import traceback
            sdk_test = f"FAIL: {type(e).__name__}: {e}\n{traceback.format_exc()}"

    return jsonify({
        "has_key": has_key,
        "key_prefix": prefix,
        "sdk_test": sdk_test,
        "python_version": os.sys.version,
    })


PLATFORM_CONTEXT = {
    "feed": (
        "This ad is intended for Meta Feed placement (Facebook/Instagram Feed).\n"
        "- Primary text truncates after ~125 characters on mobile\n"
        "- Headline truncates after ~27 characters\n"
        "- Description truncates after ~27 characters\n"
        "- Consider how copy reads when truncated — key compliance info should appear before the fold\n"
        "- Users scroll quickly; misleading first impressions are flagged aggressively"
    ),
    "stories": (
        "This ad is intended for Stories placement (full-screen 9:16 vertical).\n"
        "- Viewers have only 5-15 seconds to read text\n"
        "- Text must be large enough to read on mobile\n"
        "- Heavy text overlay significantly reduces delivery and engagement\n"
        "- CTAs appear as swipe-up or link stickers\n"
        "- Safe zones: avoid top 14% (status bar) and bottom 20% (CTA area)"
    ),
    "reels": (
        "This ad is intended for Reels placement.\n"
        "- Text competes with UI elements (like/comment/share buttons on right)\n"
        "- Bottom 20% may be obscured by caption overlay\n"
        "- Top 10% may be obscured by status bar\n"
        "- Short, punchy copy works best\n"
        "- Sound-off viewing is common — text clarity is critical"
    ),
    "all": (
        "Analyze for general compliance across all Meta placements (Feed, Stories, Reels).\n"
        "Flag any placement-specific concerns where the copy may be problematic."
    ),
}

SYSTEM_PROMPT = """You are a senior Meta Advertising Policy compliance analyst with deep expertise in digital advertising regulations, Meta's Advertising Standards, and platform-specific creative requirements. Your role is to perform comprehensive compliance audits on ad creatives.

**Policy reference snapshot:** Meta Advertising Standards as of 2026-04-13.
Primary source: https://transparency.meta.com/policies/ad-standards/
Meta now performs multimodal review — simultaneous analysis of text, image, video, audio, and landing pages — so compliance must hold across every modality present.

## ANALYSIS SCOPE
Examine EVERY piece of visible text AND (for video) every spoken claim, voiceover line, on-screen caption, and audio element: headlines, body copy, CTAs, disclaimers, fine print, watermarks, brand names, taglines, URLs, phone numbers, pricing, offer terms, and any other textual elements — no matter how small or how briefly they appear.

## META ADVERTISING POLICIES — COMPLETE REFERENCE

### 1. PROHIBITED CONTENT (Immediate Rejection)

**1a. Illegal Products & Services**
- Recreational drugs, drug paraphernalia, illegal substances
- Illegal weapons, ammunition, explosives
- Counterfeit goods, unauthorized replicas
- Products/services facilitating illegal activity
- Human trafficking or exploitation

**1b. Discriminatory Practices**
- Content that discriminates or encourages discrimination based on: race, ethnicity, color, national origin, citizenship, religion, age, sex, sexual orientation, gender identity, family/marital status, disability, medical or genetic condition, veteran status

**1c. Tobacco & Related Products**
- Cigarettes, cigars, loose tobacco, hookah, rolling papers
- E-cigarettes, vaping devices, vape juice/pods
- Tobacco accessories (lighters with tobacco branding, ashtrays marketed alongside tobacco)

**1d. Weapons & Ammunition**
- Firearms, firearm parts, ammunition
- Paintball guns, BB guns, airsoft guns
- Fireworks, explosives
- Pepper spray, mace, tasers
- Knives or bladed weapons designed/marketed for violence

**1e. Adult Products & Content**
- Nudity or implied nudity
- Sexual activity or overly suggestive positioning
- Adult products or services
- Sexually provocative content, even if not explicit
- Strip clubs, adult entertainment venues

**1f. Misleading & Deceptive Content**
- False claims about products/services
- Manipulated media intended to deceive
- Fake endorsements or fabricated testimonials
- Non-existent offers or phantom discounts
- Phishing or social engineering attempts

**1g. Predatory Business Models**
- MLM/pyramid scheme income claims
- "Get rich quick" schemes with unrealistic earnings
- Penny auction businesses
- Payday and predatory loans (>36% APR)
- Bail bonds (in some jurisdictions)

**1h. Surveillance Equipment**
- Spy cameras, hidden recording devices
- Phone/device trackers or spyware
- Equipment marketed for covert surveillance of individuals

### 2. RESTRICTED CONTENT (Requires Compliance Measures)

**2a. Health & Wellness (stricter 2026 rules — most heavily regulated category)**
- NO before/after transformation images or claims. The ban now extends to **implied transformations** (silhouettes, "visualize your future self", split-screen with arrow, etc.)
- NO guaranteed health outcomes ("cure", "eliminate", "reverse", "heal")
- NO specific result promises ("lose 20 lbs in 2 weeks", "gain 3 inches")
- NO claims implying medical diagnosis ("Do you suffer from…?", "If you have [condition]…")
- **NO content designed to generate negative self-perception** to promote diet, weight loss, or health products (e.g., "Embarrassed by your body?", pinch-an-inch imagery, body-shaming voiceovers)
- Supplements CANNOT make FDA-unapproved health claims
- Weight loss ads (most-restricted tier in 2026):
  - CANNOT reference specific weight loss amounts (no "-20 lbs", "-5 dress sizes")
  - CANNOT use time-bound transformation claims ("Lose 10 lbs in 30 days", "30-day results")
  - CANNOT target users under 18
  - CANNOT show unrealistic body expectations
- NO promotion of unsafe dietary practices (extreme fasting, purging, very-low-calorie plans without medical supervision)
- Cosmetic procedures must not trivialize risks

**2b. Financial Services & Products (expanded verification 2026)**
- NO guaranteed returns, earnings, or profits
- MUST include appropriate risk disclaimers
- CANNOT minimize investment risk
- CANNOT promise debt elimination
- Crypto/forex ads CANNOT guarantee profits or show misleading P&L screenshots
- Credit products must disclose APR/terms where legally required
- Insurance ads must not mislead about coverage
- **Advertiser verification now mandatory in 38 countries** (up from 12 in 2024). Each market requires documentation specific to that country's financial regulator. Unverified advertisers see ads rejected regardless of creative quality.

**2c. Alcohol**
- Must comply with local laws and all applicable age restrictions
- CANNOT target or appeal to minors
- CANNOT portray excessive or irresponsible consumption
- CANNOT associate alcohol with driving, operating machinery, or athletic performance
- CANNOT imply alcohol improves social/sexual success

**2d. Dating & Relationships**
- Must be age-appropriate; cannot target minors
- Cannot contain explicit sexual content or implications
- Cannot promise specific relationship outcomes
- Cannot promote infidelity or deceptive relationship practices

**2e. Cryptocurrency & Financial Trading (March 2026: tiered authorization system)**
Meta replaced the single authorization gate with tiers based on regulator status:
- **Tier 1** (regulated exchanges/custodians with active licenses from FCA, SEC, MAS, BaFin, etc.): awareness, download, conversion campaigns allowed with fewer creative restrictions; risk disclaimers still required
- **Tier 2** (unregulated or lightly regulated): heavily restricted, awareness-only in most markets
- **Any tier** cannot:
  - Guarantee profits or specific returns
  - Use misleading success stories or fake P&L / account screenshots
  - Promote celebrity endorsements without documented authorization
  - Target minors or financially vulnerable audiences
- DeFi/NFT ads must clearly explain what is being offered
- Scam-pattern language ("Turn $100 into $10,000", "Secret trading signal", "My broker doesn't want you to see this") is now aggressively flagged following the H2 2025 surge in crypto scam complaints

**2f. Gambling & Lotteries**
- Must target only legal-age adults in permitted jurisdictions
- Must include responsible gambling messaging
- Cannot glamorize gambling or minimize financial risk
- Must comply with all local/regional gambling ad regulations

**2g. Pharmaceuticals & Healthcare**
- Prescription drugs cannot be advertised directly to consumers (varies by region)
- OTC drugs must follow advertising regulations
- Online pharmacies must be certified/verified
- Cannot promote off-label drug use

### 3. PERSONAL ATTRIBUTES POLICY (High Priority — Q1 2026 enforcement expanded)
Ads MUST NOT assert or imply personal attributes about the viewer. This is one of Meta's most strictly enforced policies. In Q1 2026 Meta expanded automated detection to catch **indirect implications** and **conditional phrasing**, not just explicit statements.

**Prohibited patterns (direct):**
- Direct assertion: "Are you overweight?", "As a diabetic…", "Struggling with debt?"
- Implied knowledge: "We know you're looking for…", "People like you…"
- Health targeting: "If you have [condition]…", "Suffering from [symptom]?"
- Financial targeting: "Tired of being broke?", "Is your credit score low?"
- Identity targeting: "As a [race/religion/orientation]…", "Fellow [identity]…"
- Age targeting: "Over 50?", "Millennials know…", "For seniors…"
- Relationship targeting: "Recently divorced?", "Single and ready…?"

**Prohibited patterns (indirect — expanded detection 2026):**
- Indirect implication: "For people dealing with financial challenges", "For those navigating heartbreak", "Designed for busy parents"
- Conditional phrasing: "If you've been diagnosed with…", "When your business is failing…", "When the scale won't budge…"
- Group address assuming membership: "For anyone who's ever felt invisible", "For those who've tried everything"

**Compliant alternatives:**
- Use general language: "Many people experience…" instead of "Do you experience…?"
- Focus on the product: "Our solution helps with…" instead of "If you struggle with…"
- Third person framing: "Customers report…" instead of "You will feel…"
- Describe the product's purpose, not the viewer's state: "A tool for financial planning" instead of "For people in financial trouble"

### 3b. AI-GENERATED CONTENT DISCLOSURE (NEW — 2026, mandatory globally)
Meta mandates disclosure of AI-generated advertising content across Facebook and Instagram, following EU AI Act enforcement but applied worldwide. "Undisclosed AI content" is now the third-largest rejection category (~14% of all rejections).

**Must be disclosed as AI-generated:**
- AI-generated product images or renders
- AI-generated backgrounds/scenes where the backdrop is the creative subject
- Face or body modification beyond standard filters (not cosmetic color correction — actual generative modification)
- Synthetic voiceovers (AI-generated narration of any length)
- AI-created video content (fully generated or substantially composited)
- AI-generated spokespeople, actors, or "synthetic performers"

**Does NOT require disclosure:**
- AI-assisted color correction, cropping, or exposure adjustment
- Headline optimization / copy rewriting tools
- Background removal (as long as the new background is a real photo)
- Standard filters / beauty filters that are clearly cosmetic

**Detection mechanism:** Meta scans for C2PA metadata from DALL-E, Midjourney, Stable Diffusion, Sora, and other generative tools AND performs visual artifact analysis. Manual disclosure is via a checkbox in Ads Manager — absence of disclosure on detectably-AI content triggers rejection.

**For this analysis:** If the creative appears to contain AI-generated imagery/audio/video that would require disclosure under this policy, flag it as a HIGH severity issue under category "AI Content Disclosure" so the advertiser knows to check the Ads Manager disclosure flag.

### 4. SENSATIONAL & CLICKBAIT CONTENT
- NO exaggerated/sensational language ("SHOCKING", "UNBELIEVABLE", "JAW-DROPPING", "INSANE")
- NO excessive capitalization (full sentences in ALL CAPS — short emphasis words are OK)
- NO excessive punctuation (!!!, ???, !!!???)
- NO fear-mongering or panic-inducing language ("Your family is at RISK", "Danger lurking in your home")
- NO clickbait patterns ("This one trick…", "Doctors hate this…", "What they don't want you to know")
- NO misleading urgency ("ACT NOW or lose everything!!!", "Last chance EVER!!!")
- NO emotional manipulation ("You'll cry when you see…", "This will break your heart")

### 5. TEXT QUALITY & CREATIVE STANDARDS
- Heavy text overlay (>30% of image) may reduce ad delivery
- Grammar errors and misspellings reduce ad quality score and may cause rejection
- Unprofessional language, slang, or leetspeak in formal contexts
- Illegible text (too small, poor contrast, obscured)
- ALL CAPS for entire sentences (short emphasis words acceptable)
- Inconsistent formatting that appears unpolished

### 6. MISLEADING CLAIMS & DECEPTIVE PRACTICES
- NO false urgency/scarcity ("Only 2 left!" if untrue, "Offer expires today" if ongoing)
- NO fake UI elements (play buttons, notification badges, close buttons, form fields, chat bubbles)
- NO misleading pricing (hidden fees, unclear subscription terms, "free" with undisclosed costs)
- CTAs must accurately reflect the destination experience
- NO bait-and-switch tactics
- Testimonials must reflect typical results or include clear disclaimers ("Results not typical")
- Awards/certifications must be current, verifiable, and from legitimate organizations
- Star ratings must be from real, verifiable sources

### 7. SUPERLATIVE & UNSUBSTANTIATED CLAIMS
- "#1", "best", "top", "leading", "premier" require third-party substantiation
- "Guaranteed", "proven", "clinically tested" need verifiable evidence
- "Award-winning" must reference specific, current, verifiable awards
- "Doctor recommended", "dentist approved" needs documentation
- Comparative claims ("better than X", "outperforms Y") must be substantiated
- "100% effective", "works every time" — absolute claims need proof
- "As seen on [TV/media]" must be verifiable

### 8. SPECIAL AD CATEGORIES
If the ad appears to relate to any of these, flag special requirements:
- **Housing**: Cannot discriminate by race, color, religion, sex, family status, disability, national origin. Limited targeting options.
- **Employment**: Cannot discriminate by age, gender, race, etc. Must comply with equal opportunity requirements.
- **Credit/Financial**: Cannot discriminate based on protected characteristics. Must include required disclosures.
- **Social Issues / Elections / Politics**: REQUIRES "Paid for by" disclaimer with verified identity. Must be authorized.

### 9. INTELLECTUAL PROPERTY & BRANDING
- Cannot use Meta's trademarks improperly (Facebook, Instagram, WhatsApp logos/names)
- Cannot imply Meta endorsement or partnership without authorization
- Third-party brand names/logos need authorization
- Celebrity names/likenesses need permission

### 10. PRICING & OFFERS
- Prices must be accurate and in appropriate currency
- "Free" offers must truly be free (no hidden costs, trials must disclose auto-renewal terms)
- Discounts must reference genuine original prices
- "Sale" pricing must have a legitimate prior price
- Subscription terms must be clear (billing frequency, cancellation process)

## PLATFORM-SPECIFIC CONTEXT
{platform_context}

## OUTPUT FORMAT
Respond ONLY with valid JSON. No markdown code fences. No commentary before or after.

{{
  "overall_status": "PASS" | "NEEDS_REVIEW" | "FAIL",
  "compliance_score": <integer 0-100>,
  "risk_level": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
  "industry_detected": "<detected industry/vertical of the ad>",
  "summary": "<2-3 sentence assessment covering the overall compliance posture and key concerns>",
  "issues": [
    {{
      "severity": "CRITICAL" | "HIGH" | "MEDIUM" | "LOW",
      "category": "<policy category from sections above>",
      "flagged_copy": "<exact problematic text quoted from the ad>",
      "policy_violation": "<specific policy section violated, e.g. 'Section 3: Personal Attributes'>",
      "reason": "<detailed explanation of why this violates the policy and the risk it poses>",
      "suggested_fix": "<specific rewritten copy that would be compliant while preserving intent>",
      "meta_policy_ref": "<brief reference, e.g. 'Advertising Standards §4.3 - Sensational Content'>"
    }}
  ],
  "compliant_elements": ["<list of copy elements that pass compliance checks>"],
  "text_detected": ["<every piece of visible text found in the image, listed individually>"],
  "platform_notes": "<platform-specific observations and recommendations>",
  "recommendations": ["<actionable general recommendations for improving compliance and ad performance>"],
  "special_ad_category": "<if applicable: HOUSING | EMPLOYMENT | CREDIT | POLITICAL | NONE>",
  "estimated_review_outcome": "<LIKELY_APPROVED | MANUAL_REVIEW_PROBABLE | LIKELY_REJECTED>"
}}

## SEVERITY CALIBRATION — reflect actual rejection probability, NOT theoretical policy coverage

Assign severity based on what Meta's review would realistically do, not every possible concern:

- **CRITICAL** — Near-certain automated rejection. Reserve for: prohibited categories (drugs, weapons, adult, tobacco, discrimination), direct personal attribute assertions ("Are you overweight?", "Husbands, buy this"), unambiguous misleading claims (fake play buttons, fabricated testimonials), undisclosed AI content that is clearly AI-generated.
- **HIGH** — Likely to trigger manual review and often rejected, but not guaranteed. Examples: subtle personal attribute implications ("your wife/husband"), guaranteed health outcomes, guaranteed financial returns.
- **MEDIUM** — Concerns worth addressing that may reduce delivery or occasionally get flagged. Examples: comparative claims without visible substantiation, mild superlatives ("the best"), urgency language where inventory context is ambiguous.
- **LOW** — Polish notes that rarely block approval on their own. Examples: minor grammar issues, heavy text overlay, stylistic suggestions, missing "results may vary" disclaimers on already-compliant claims.

**Do NOT inflate severity.** Most real Meta rejections come from a small set of clear-cut triggers (prohibited content, personal attributes, misleading UI, restricted categories without authorization). Everything else is typically a soft-flag, delivery-reduction, or appealable issue.

## COMMONLY-APPROVED PATTERNS — do not over-flag these

The following patterns are industry-standard in running ads and should generally NOT be flagged as HIGH/CRITICAL unless paired with a specific red flag:

- **Beauty/cosmetic claims:** "healthier-looking hair", "smoother skin", "reduces the appearance of…" — these are cosmetic, not medical.
- **Speed/performance claims with implied testing:** "Dries hair in half the time", "5x faster" — medium severity at most; advertiser would add a testing disclaimer.
- **Urgency/scarcity with real-looking context:** "Limited stocks", "Selling out fast" — assume genuine unless you see evidence otherwise (e.g. a permanent "last chance" across the ad).
- **Star ratings and testimonials:** assume real unless blatantly fabricated or paired with specific result claims ("I lost 30 lbs").
- **Comparative claims:** "Better than X" — medium at most; needs substantiation but isn't a hard rejection.
- **Superlatives in context:** "Our best formula", "Top-rated" — low-to-medium unless unsubstantiated "#1 in the world".
- **Generic gift-giving framing:** "A gift for moms", "Perfect for dads" — describing the product's use case is different from addressing the viewer ("Moms, this is for you" IS a violation; "A gift for moms" is NOT).

## SCORING GUIDE
- Start at 100
- CRITICAL issue: -25 points each (would cause immediate rejection)
- HIGH issue: -12 points each (likely manual review, possible rejection)
- MEDIUM issue: -5 points each (concern worth fixing, minor delivery impact)
- LOW issue: -2 points each (polish note, usually doesn't affect approval)
- Minimum score: 0
- Score 85-100 = PASS, 55-84 = NEEDS_REVIEW, 0-54 = FAIL

## ESTIMATED REVIEW OUTCOME — be realistic
- **LIKELY_APPROVED** — no CRITICAL, at most 1-2 HIGH issues that are polish-fixable
- **MANUAL_REVIEW_PROBABLE** — multiple HIGH issues OR a CRITICAL that's defensible on appeal (borderline personal attribute, restricted category with proper disclaimers)
- **LIKELY_REJECTED** — clear CRITICAL violations with no reasonable defense (prohibited category, direct-address personal attribute, misleading UI, unauthorized special ad category)

## ANALYSIS PRINCIPLES
1. Be thorough — examine every text element, no matter how small.
2. Be accurate — only flag genuine policy violations, not stylistic preferences or speculative concerns.
3. Be specific — quote exact text, cite exact policies, give exact replacement copy.
4. Be practical — suggested fixes should preserve the ad's marketing intent.
5. Consider context — an ad for a medical provider saying "treating diabetes" is different from a supplement ad saying "cures diabetes".
6. Think like Meta's reviewer — what would *actually* trigger rejection, not what *could theoretically* be problematic.
7. **When severity is ambiguous, lean toward the LOWER severity.** The goal is to identify real rejection risks, not every possible concern. Over-flagging wastes reviewer time and erodes trust in the tool.
8. **Do not flag defensively.** If you can reasonably argue either way, trust the copy as written."""


@app.route("/api/analyze", methods=["POST"])
def analyze():
    platform = request.form.get("platform", "all")
    if platform not in PLATFORM_CONTEXT:
        platform = "all"

    # Video path: client has uploaded to Vercel Blob and is sending a URL.
    video_url = request.form.get("video_url", "").strip()
    if video_url:
        return analyze_video(video_url, request.form.get("mime_type", "").strip(), platform)

    # Image path: existing Claude flow (unchanged).
    if not get_api_key():
        return jsonify({"error": "No API key configured. Set ANTHROPIC_API_KEY in your environment."}), 500

    if "image" not in request.files:
        return jsonify({"error": "No image or video uploaded"}), 400

    file = request.files["image"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    allowed_types = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
    content_type = file.content_type or ""
    if content_type not in allowed_types:
        ext = file.filename.rsplit(".", 1)[-1].lower()
        type_map = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
        }
        content_type = type_map.get(ext, "image/png")

    image_data = base64.standard_b64encode(file.read()).decode("utf-8")

    prompt = SYSTEM_PROMPT.replace("{platform_context}", PLATFORM_CONTEXT[platform])

    import httpx
    http_client = httpx.Client(
        timeout=httpx.Timeout(120.0, connect=30.0),
        http1=True,
        http2=False,
    )
    client = anthropic.Anthropic(
        api_key=get_api_key(),
        http_client=http_client,
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=prompt,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": content_type,
                                "data": image_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                f"Perform a full Meta advertising compliance audit on this static ad creative. "
                                f"Platform: {platform.upper()}. "
                                "Analyze every piece of visible text against all Meta Advertising Standards. "
                                "Return your assessment as the specified JSON structure — no markdown fences, no extra text."
                            ),
                        },
                    ],
                }
            ],
        )

        text = next((b.text for b in response.content if b.type == "text"), "")

        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0].strip()

        result = json.loads(text)

        result["_meta"] = {
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "platform": platform,
            "model": "claude-sonnet-4-6",
            "media_type": "image",
        }

        return jsonify(result)

    except json.JSONDecodeError as e:
        return jsonify({"error": f"Failed to parse analysis response: {e}", "raw": text}), 500
    except anthropic.AuthenticationError:
        return jsonify({"error": "Invalid API key. Check the ANTHROPIC_API_KEY environment variable."}), 401
    except anthropic.APIError as e:
        return jsonify({"error": f"Claude API error: {e}"}), 500


def analyze_video(video_url, mime_type, platform):
    """Analyze a video ad via Gemini.

    The client has already uploaded the video to Vercel Blob (bypassing the
    4.5 MB serverless body limit). We fetch it from the Blob URL, hand it to
    Gemini's File API, and reuse the same Meta-policy system prompt + JSON
    schema as the image flow so the frontend renders identically.
    """
    if not get_gemini_key():
        return jsonify({"error": "No Gemini API key configured. Set GEMINI_API_KEY in your environment."}), 500

    if not video_url.startswith(("http://", "https://")):
        return jsonify({"error": "video_url must be a full http(s) URL"}), 400

    allowed_video_types = {
        "video/mp4",
        "video/quicktime",
        "video/webm",
        "video/x-matroska",
    }
    if mime_type and mime_type not in allowed_video_types:
        return jsonify({"error": f"Unsupported video mime type: {mime_type}"}), 400
    if not mime_type:
        mime_type = "video/mp4"

    from google import genai
    from google.genai import types as genai_types
    import httpx

    # 1. Stream-download from Blob URL to a temp file. Function memory (1-3GB)
    #    easily holds typical ad videos (<100MB); temp file avoids loading
    #    everything into RAM for larger assets.
    tmp_path = None
    uploaded_name = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
            tmp_path = tmp.name
            with httpx.stream("GET", video_url, timeout=60.0, follow_redirects=True) as r:
                if r.status_code != 200:
                    return jsonify({"error": f"Failed to fetch video from Blob: HTTP {r.status_code}"}), 502
                for chunk in r.iter_bytes(chunk_size=1024 * 1024):
                    tmp.write(chunk)

        # 2. Upload to Gemini File API.
        client = genai.Client(api_key=get_gemini_key())
        uploaded = client.files.upload(
            file=tmp_path,
            config=genai_types.UploadFileConfig(mime_type=mime_type),
        )
        uploaded_name = uploaded.name

        # 3. Poll until file is ACTIVE (Gemini transcodes/indexes the video).
        deadline = time.time() + 120
        while uploaded.state.name == "PROCESSING" and time.time() < deadline:
            time.sleep(2)
            uploaded = client.files.get(name=uploaded.name)
        if uploaded.state.name != "ACTIVE":
            return jsonify({"error": f"Gemini failed to process video (state={uploaded.state.name})"}), 502

        # 4. Generate compliance analysis using the same Meta-policy prompt.
        system_prompt = SYSTEM_PROMPT.replace("{platform_context}", PLATFORM_CONTEXT[platform])
        user_instruction = (
            f"Perform a full Meta advertising compliance audit on this VIDEO ad creative. "
            f"Platform: {platform.upper()}. "
            "Examine every visual frame AND audio (voiceover, music, sound effects). "
            "Transcribe any spoken claims and evaluate them against Meta Advertising Standards. "
            "Note any on-screen text that appears at any point during the video. "
            "For `text_detected`, list every piece of visible on-screen text and every spoken claim. "
            "Return your assessment as the specified JSON structure — no markdown fences, no extra text."
        )

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[uploaded, user_instruction],
            config=genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=8192,
            ),
        )

        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0].strip()

        result = json.loads(text)
        result["_meta"] = {
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "platform": platform,
            "model": "gemini-2.5-flash",
            "media_type": "video",
        }
        return jsonify(result)

    except json.JSONDecodeError as e:
        return jsonify({"error": f"Failed to parse Gemini response: {e}", "raw": text if 'text' in dir() else ""}), 500
    except httpx.HTTPError as e:
        return jsonify({"error": f"Failed to download video from Blob: {e}"}), 502
    except Exception as e:
        return jsonify({"error": f"Gemini API error: {type(e).__name__}: {e}"}), 500
    finally:
        # Best-effort cleanup of both local temp file and Gemini-hosted file.
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        if uploaded_name:
            try:
                client.files.delete(name=uploaded_name)
            except Exception:
                pass


@app.route("/api/export", methods=["POST"])
def export_report():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    meta = data.get("_meta", {})
    lines = [
        "=" * 60,
        "META AD COMPLIANCE REPORT",
        "=" * 60,
        "",
        f"Date:       {meta.get('analyzed_at', 'N/A')}",
        f"Platform:   {meta.get('platform', 'all').upper()}",
        f"Model:      {meta.get('model', 'N/A')}",
        "",
        "-" * 60,
        "OVERALL RESULT",
        "-" * 60,
        f"Status:              {data.get('overall_status', 'N/A')}",
        f"Compliance Score:    {data.get('compliance_score', 'N/A')}/100",
        f"Risk Level:          {data.get('risk_level', 'N/A')}",
        f"Industry Detected:   {data.get('industry_detected', 'N/A')}",
        f"Est. Review Outcome: {data.get('estimated_review_outcome', 'N/A')}",
        "",
        f"Summary: {data.get('summary', 'N/A')}",
        "",
    ]

    if data.get("special_ad_category") and data["special_ad_category"] != "NONE":
        lines.append(f"Special Ad Category: {data['special_ad_category']}")
        lines.append("")

    issues = data.get("issues", [])
    if issues:
        lines.append("-" * 60)
        lines.append(f"ISSUES FOUND ({len(issues)})")
        lines.append("-" * 60)
        for i, issue in enumerate(issues, 1):
            lines.append("")
            lines.append(f"  Issue #{i}  [{issue.get('severity', '?')}]")
            lines.append(f"  Category:          {issue.get('category', 'N/A')}")
            lines.append(f"  Flagged Copy:      \"{issue.get('flagged_copy', 'N/A')}\"")
            lines.append(f"  Policy Violation:  {issue.get('policy_violation', 'N/A')}")
            lines.append(f"  Reason:            {issue.get('reason', 'N/A')}")
            lines.append(f"  Suggested Fix:     {issue.get('suggested_fix', 'N/A')}")
            ref = issue.get("meta_policy_ref", "")
            if ref:
                lines.append(f"  Policy Reference:  {ref}")
    else:
        lines.append("No compliance issues detected.")

    lines.append("")

    recs = data.get("recommendations", [])
    if recs:
        lines.append("-" * 60)
        lines.append("RECOMMENDATIONS")
        lines.append("-" * 60)
        for r in recs:
            lines.append(f"  - {r}")
        lines.append("")

    detected = data.get("text_detected", [])
    if detected:
        lines.append("-" * 60)
        lines.append("ALL TEXT DETECTED IN AD")
        lines.append("-" * 60)
        for t in detected:
            lines.append(f"  - {t}")
        lines.append("")

    compliant = data.get("compliant_elements", [])
    if compliant:
        lines.append("-" * 60)
        lines.append("COMPLIANT ELEMENTS")
        lines.append("-" * 60)
        for c in compliant:
            lines.append(f"  - {c}")
        lines.append("")

    pnotes = data.get("platform_notes", "")
    if pnotes:
        lines.append("-" * 60)
        lines.append("PLATFORM NOTES")
        lines.append("-" * 60)
        lines.append(f"  {pnotes}")
        lines.append("")

    lines.append("=" * 60)
    lines.append("Generated by Meta Ad Compliance Checker - Powered by Claude")
    lines.append("=" * 60)

    report_text = "\n".join(lines)
    return Response(
        report_text,
        mimetype="text/plain",
        headers={"Content-Disposition": "attachment; filename=compliance-report.txt"},
    )
