---
title: "What an AI Music Video Actually Costs: Per-Scene Numbers for 8 Video Models"
description: "A three-minute AI music video is about 30 scenes, not one video. Here is what a scene costs on Seedance, Kling, MiniMax and Grok — in dollars, with the arithmetic shown."
slug: "ai-music-video-cost"
lang: "en"
translationKey: "cost"
date: "2026-08-21"
updated: "2026-08-21"
cover: "/img/shots/step-clip.jpg"
tags: ["pricing", "engines", "music video"]
---

# What an AI Music Video Actually Costs

Every AI video platform prices in its own currency. Credits, points, coins, "generations", "seconds of render". This is not an accident — it makes the offers impossible to line up side by side. You cannot tell whether 1,000 credits is a good deal until you know what one finished thing costs in them.

So here is the same question asked in dollars, for a specific deliverable: **one three-minute vertical music video**. The numbers below are the August 2026 price list we pay our model provider (kie.ai) for a six-second 9:16 scene with audio generation switched off. They are a price list, not a bill — real consumption gets calibrated against provider logs after a run — but they are the same numbers our own pricing is built on, and you can check them against any aggregator.

## A music video is 30 scenes, not one video

This is the part most pricing guides skip. No current model generates three coherent minutes. Every real pipeline cuts the song into shots and generates them one at a time.

Our default scene length is **six seconds**, which is roughly where the models stop drifting and where a cut still feels musical. Three minutes at six seconds a scene is **30 scenes**. Every one of them is a separate paid generation.

That single fact reorders the whole cost conversation. A model that looks cheap at "$0.30 per video" is $9 for a clip. A model at $1.89 a scene is $56.70 for the same clip. Same interface, same two clicks, 6× the invoice.

## The price list, per scene and per finished clip

Six-second scene, 9:16 vertical, no generated audio:

| Model | Per 6-second scene | Per second | 3-minute clip (30 scenes) |
|---|---|---|---|
| Grok (our own gateway) | $0.00 | $0.00 | $0.00 |
| Seedance 2 Mini · 720p | $0.246 | $0.041 | **$7.38** |
| Kling 3.0 · 720p | $0.42 | $0.070 | **$12.60** |
| MiniMax H3 · 768p | $0.48 | $0.080 | **$14.40** |
| Kling 3.0 Pro · 1080p | $0.54 | $0.090 | **$16.20** |
| Seedance 2.5 · 480p | $0.84 | $0.140 | **$25.20** |
| Seedance 2.0 · 720p | $1.23 | $0.205 | **$36.90** |
| Seedance 2.5 · 720p | $1.89 | $0.315 | **$56.70** |

The spread between the cheapest paid model and the most expensive one is **7.7×** for the same 180 seconds of footage. That is the single biggest lever you have over the cost of a clip, and it is a dropdown.

Two notes that matter more than they look:

**Switch generated audio off.** Seedance will produce a soundtrack by default. On a music video that track is garbage — you are laying your own song over the top — and on some providers it costs extra. Our pipeline sends `generate_audio: false` on every call for exactly this reason.

**480p is the same model, halved.** Seedance 2.5 at 480p is $0.84 against $1.89 for the same model at 720p. You lose resolution, not the model's motion quality. For a Reel that gets watched on a phone at arm's length, that trade is often free money.

## Frames are a separate line item

A frame-to-video model does not invent your shot — it animates a picture you give it. Our pipeline gives each scene **two** pictures: the first frame and the last frame, with motion generated between them ([why that matters](/blog/first-and-last-frame/)).

So every scene carries an image bill on top of the video bill:

| Image model | Per image | Per scene (2 frames) | Per 3-minute clip |
|---|---|---|---|
| Gateway models (ChatGPT / Grok) | $0.00 | $0.00 | $0.00 |
| Nano Banana (edit) | $0.02 | $0.04 | $1.20 |
| Nano Banana 2 · 2K | $0.06 | $0.12 | $3.60 |
| Nano Banana Pro · 1K–2K | $0.09 | $0.18 | **$5.40** |

Nano Banana Pro is not on the list because of leaderboard position — blind tests put other image models above it. It is there for engineering reasons: up to 8 separate reference images instead of one glued-together collage, native vertical 1K/2K/4K without an upscale pass, and parallel jobs instead of one browser session queued behind itself. When a character has to survive 30 scenes, references are the whole game.

So the honest total for a full-quality three-minute clip — Nano Banana Pro frames plus Seedance 2.5 at 720p — is **$62.10**. The same clip on Kling 3.0 Pro is **$21.60**. On Seedance 2 Mini with gateway frames it is **$7.38**. On the free Grok path it is **$0.00**.

## The cost nobody quotes: retries

Every price list assumes each generation is the one you keep. In practice a scene gets rejected because a hand went wrong, a face drifted, or the camera moved the opposite way from what the prompt asked. Budget **20–30% on top** of the numbers above and you will be close.

This is where architecture beats price. Two things cut the retry bill in half:

1. **Redraw one frame, not the scene.** If the last frame is wrong, regenerating it costs one image ($0.09), not one image plus a video ($1.98). Our scene cards rebuild a single frame on its own for exactly this reason.
2. **Fix the storyboard before you draw anything.** Rejecting a shot on the sketch sheet costs nothing. Rejecting it after it has been animated costs the full scene.

The order of operations is the cost control. A pipeline that jumps straight from a prompt to a finished video gives you nothing to reject cheaply.

## How this becomes credits on lolq.ai

We use credits too — but ours are pinned to cost, not invented. One credit is **$0.0125 of engine cost**, and every price is computed from the dollar figures above and rounded up. That is a mechanical rule in the code, not a marketing promise: a scene's credit price cannot drift away from what the scene costs us.

Scene prices in credits, gateway frames (free plan and PRO):

| Engine | Video | + frames | Scene |
|---|---|---|---|
| Grok | 2 | 2 | **4** |
| Seedance 2 Mini | 20 | 2 | **22** |
| Kling 3.0 | 34 | 2 | **36** |
| Kling 3.0 Pro | 44 | 2 | **46** |
| Seedance 2.5 · 480p | 68 | 2 | **70** |
| Seedance 2.0 | 99 | 2 | **101** |
| Seedance 2.5 · 720p | 152 | 2 | **154** |

On plans with Nano Banana Pro frames, add 13 credits to each line (15 instead of 2).

## What actually fits in a plan

Multiply and you get the honest answer, which is not always the flattering one:

- **FREE — 150 credits.** A Grok scene is 4. Thirty of them is 120: **one complete three-minute clip**, on the house, no card and no signup — and 30 credits left over for the storyboard sheet, a character model and a redo.
- **PRO — $20, 660 credits.** A Seedance 2 Mini scene is 22. Thirty of them is 660: **one full clip a month** with real first-to-last-frame motion.
- **PRO MAX — $100, 3,400 credits.** Enough for **about three clips** on Seedance 2 Mini, or **two** on Kling 3.0 Pro. A full clip on Seedance 2.5 at 720p does **not** fit — 3,400 credits buys 20 scenes of it, two thirds of a song. Use 2.5 for the shots that carry the video and a cheaper engine for the rest.
- **STUDIO — $299, 10,500 credits.** **Two full clips** on Seedance 2.5 at 720p, or about six on Kling 3.0 Pro.

We would rather write that down than let you find it out on scene 21.

## The cheapest honest route to a finished clip

If the goal is a finished video and not a benchmark:

1. Build the whole thing on the **free Grok path** first. Story, storyboard, all 30 scenes. It costs nothing and it tells you whether the idea works.
2. Keep the storyboard, swap the engine, and **regenerate only the shots that carry the song** — the hook, the drop, the two or three frames people will screenshot.
3. Leave the connective shots on the cheap engine. In a nine-second-attention-span format, nobody is grading the B-roll.

A clip built this way lands between $3 and $8 of engine cost instead of $62, and the difference is invisible on a phone.

## Try the arithmetic on your own track

The free plan is a real one, not a watermark: 150 credits, one full three-minute clip with room to spare, no signup for the first one. Upload an mp3, pick a style, and watch the credit counter move on every step — every action shows what it will cost before you press it.

[Open the studio →](/) · [See the plans and packs](/#ld-pricing) · [How the four steps work](/#ld-how)

**Related:** [Seedance 2.5 vs Kling 3.0 vs Grok — which engine to animate with](/blog/seedance-vs-kling-vs-grok/) · [First and last frame explained](/blog/first-and-last-frame/) · [Make a music video from your own track for free](/blog/make-music-video-from-your-track-free/)
