import os
import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, request, jsonify, Response
import anthropic

app = Flask(__name__)

INDEX_HTML = (Path(__file__).resolve().parent / "index.html").read_text()


@app.route("/")
def serve_index():
    return Response(INDEX_HTML, content_type="text/html")


def get_api_key():
    return os.environ.get("ANTHROPIC_API_KEY", "")


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

SYSTEM_PROMPT = """You are a senior Meta Advertising Policy compliance analyst with deep expertise in digital advertising regulations, Meta's Advertising Standards, and platform-specific creative requirements. Your role is to perform comprehensive compliance audits on static ad creatives.

## ANALYSIS SCOPE
Examine EVERY piece of visible text: headlines, body copy, CTAs, disclaimers, fine print, watermarks, brand names, taglines, URLs, phone numbers, pricing, offer terms, and any other textual elements — no matter how small.

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

**2a. Health & Wellness**
- NO before/after transformation images or claims
- NO guaranteed health outcomes ("cure", "eliminate", "reverse", "heal")
- NO specific result promises ("lose 20 lbs in 2 weeks", "gain 3 inches")
- NO claims implying medical diagnosis ("Do you suffer from…?", "If you have [condition]…")
- Supplements CANNOT make FDA-unapproved health claims
- Weight loss ads CANNOT contain unrealistic body expectations
- NO promotion of unsafe dietary practices (extreme fasting, purging)
- Cosmetic procedures must not trivialize risks

**2b. Financial Services & Products**
- NO guaranteed returns, earnings, or profits
- MUST include appropriate risk disclaimers
- CANNOT minimize investment risk
- CANNOT promise debt elimination
- Crypto/forex ads CANNOT guarantee profits or show misleading P&L screenshots
- Credit products must disclose APR/terms where legally required
- Insurance ads must not mislead about coverage

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

**2e. Cryptocurrency & Financial Trading**
- Cannot guarantee profits or specific returns
- Must include risk disclaimers
- Cannot use misleading success stories or fake account screenshots
- DeFi/NFT ads must clearly explain what is being offered

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

### 3. PERSONAL ATTRIBUTES POLICY (High Priority)
Ads MUST NOT assert or imply personal attributes about the viewer. This is one of Meta's most strictly enforced policies.

**Prohibited patterns:**
- Direct assertion: "Are you overweight?", "As a diabetic…", "Struggling with debt?"
- Implied knowledge: "We know you're looking for…", "People like you…"
- Health targeting: "If you have [condition]…", "Suffering from [symptom]?"
- Financial targeting: "Tired of being broke?", "Is your credit score low?"
- Identity targeting: "As a [race/religion/orientation]…", "Fellow [identity]…"
- Age targeting: "Over 50?", "Millennials know…", "For seniors…"
- Relationship targeting: "Recently divorced?", "Single and ready…?"

**Compliant alternatives:**
- Use general language: "Many people experience…" instead of "Do you experience…?"
- Focus on the product: "Our solution helps with…" instead of "If you struggle with…"
- Third person framing: "Customers report…" instead of "You will feel…"

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

## SCORING GUIDE
- Start at 100
- CRITICAL issue: -25 points each (would cause immediate rejection)
- HIGH issue: -15 points each (likely rejection or major delivery reduction)
- MEDIUM issue: -10 points each (may trigger manual review)
- LOW issue: -5 points each (minor concern, may reduce quality score)
- Minimum score: 0
- Score 90-100 = PASS, 60-89 = NEEDS_REVIEW, 0-59 = FAIL

## ANALYSIS PRINCIPLES
1. Be thorough — examine every text element, no matter how small
2. Be accurate — only flag genuine policy violations, not stylistic preferences
3. Be specific — quote exact text, cite exact policies, give exact replacement copy
4. Be practical — suggested fixes should preserve the ad's marketing intent
5. Consider context — an ad for a medical provider saying "treating diabetes" is different from a supplement ad saying "cures diabetes"
6. Think like Meta's reviewer — what would trigger automated rejection or manual review?
7. When severity is ambiguous, lean toward the higher severity — it's better to over-flag than to miss a rejection trigger"""


@app.route("/api/analyze", methods=["POST"])
def analyze():
    if not get_api_key():
        return jsonify({"error": "No API key configured. Set ANTHROPIC_API_KEY in your environment."}), 500

    if "image" not in request.files:
        return jsonify({"error": "No image uploaded"}), 400

    file = request.files["image"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    platform = request.form.get("platform", "all")
    if platform not in PLATFORM_CONTEXT:
        platform = "all"

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

    client = anthropic.Anthropic(
        api_key=get_api_key(),
        timeout=120.0,
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
        }

        return jsonify(result)

    except json.JSONDecodeError as e:
        return jsonify({"error": f"Failed to parse analysis response: {e}", "raw": text}), 500
    except anthropic.AuthenticationError:
        return jsonify({"error": "Invalid API key. Check the ANTHROPIC_API_KEY environment variable."}), 401
    except anthropic.APIError as e:
        return jsonify({"error": f"Claude API error: {e}"}), 500


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
