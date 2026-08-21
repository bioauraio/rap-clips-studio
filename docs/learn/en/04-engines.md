---
title: "Video engines: Grok, Seedance, Kling"
description: "What you are paying for when a scene costs 4 credits and when it costs 154, and how mixing engines inside one clip doubles what a plan gets you."
slug: engines
translationKey: engines
lang: en
level: 2
access: free
minutes: 6
cover: /img/guide/anim.png
tags: [engines, credits, pricing]
date: 2026-08-21
updated: 2026-08-21
---

# Video engines: Grok, Seedance, Kling

A scene is two purchases: **the pair of frames** and **the video made from them.**
They are priced separately because they are drawn by different engines, and
knowing that is most of what you need to spend well.

## What a scene costs

Frames, per scene:

| Frame engine | Pair of frames | Plans |
|---|---|---|
| Gateway (ChatGPT / Grok) | **2** | FREE, PRO |
| Nano Banana Pro, native vertical 2K | **15** | PRO MAX, STUDIO |

Video, per 6-second scene:

| Engine | Video | First + last frame? |
|---|---|---|
| Grok | **2** | no — animates the first frame only |
| Seedance 2 Mini · 720p | **20** | yes |
| Kling 3.0 · 720p | **34** | yes |
| MiniMax H3 · 768p | **39** | yes |
| Kling 3.0 Pro · 1080p | **44** | yes |
| Seedance 2.5 · 480p | **68** | yes |
| Seedance 2.0 · 720p | **99** | yes |
| Seedance 2.5 · 720p | **152** | yes |

Add the two together for the scene. On the free plan: 2 + 2 = **4 credits a
scene**. On PRO MAX with Seedance 2.5: 15 + 152 = **167 credits a scene**.

These numbers are not a markup we invented. Every one of them is the engine's own
price to us, converted at a fixed rate and rounded up. When Seedance 2.5 costs
forty times what Grok costs, that is because it costs us forty times more.

## The difference that actually matters

Not resolution. **Whether the engine uses your last frame.**

**Grok** animates the first frame and invents the rest. You get motion, you do not
get a destination. The scene ends wherever the model decided. It runs on our own
subscription, which is why it is nearly free.

**Everything else** interpolates between your first and your last frame. You draw
where the scene starts and where it ends, and the engine finds the path between
them. That is the difference between "something moves" and directing a shot.

This is why the free plan is honest but limited: it can make a whole clip, and it
cannot make you a director yet.

## Which one to reach for

**Grok** — establishing shots, texture, crowds, anything atmospheric where nothing
specific has to happen. Also every scene of a draft. It is cheap enough that you
can animate a whole clip just to see whether the edit holds together.

**Seedance 2 Mini** — the workhorse. First-and-last interpolation at a tenth of the
price of the flagship. If you are producing regularly rather than showing off,
most of your finished clips should be made of this.

**Kling 3.0 / Kling 3.0 Pro** — stronger with human bodies and camera moves. Pro
gives you 1080p. Reach for it when a scene has a person doing something physical
and Seedance keeps warping them.

**MiniMax H3** — sits between Kling and the expensive Seedances. Worth trying on
scenes where the other two both fail; it fails differently.

**Seedance 2.5 · 480p** — the flagship model at half price, paying for it in
resolution. On a vertical clip that lives on a phone, 480p upscales better than
people expect. This is the value pick on PRO MAX.

**Seedance 2.0 and Seedance 2.5 · 720p** — the top of the market and priced like
it. These are for the three or four scenes that carry the clip: the opening, the
hook, the one shot people will screenshot. A whole clip on Seedance 2.5 is two
thirds of a PRO MAX month.

## Mixing engines inside one clip

Each scene card has its own **video engine** selector — Seedance (2 frames) or
Grok (1 frame). The choice is per scene, and this is the single biggest lever on
how far a plan goes.

Take PRO, which gives you 660 credits a month. A thirty-scene clip entirely on
Seedance 2 Mini costs 30 × 22 = **660**. That is your whole month, one clip.

Now split it: twenty ordinary scenes on Grok, ten scenes that matter on Seedance 2
Mini.

> 20 × 4 + 10 × 22 = 80 + 220 = **300 credits.**

Two clips a month instead of one, and the ten scenes people actually remember are
still on the good engine. The same arithmetic works on every plan.

The honest caveat: mixed engines look mixed. Grok's motion is looser and its
resolution is lower. Put the Grok scenes where the eye is not resting — fast cuts,
wide shots, texture — and put the interpolated scenes on faces and on movement.

## What a plan gets you

A three-minute clip is about 30 scenes.

**FREE · 150 credits.** Grok only, gateway frames. One three-minute clip is 120
of that, so you have a small margin for sheets and redos. See
[Your first clip](00-first-clip.md) for how to spend it without hitting the wall.

**PRO · 660 credits.** Adds Seedance 2 Mini. One full clip on Seedance, or two to
three if you mix in Grok.

**PRO MAX · 3400 credits.** Nano Banana Pro frames — native vertical 2K, and up to
eight separate references instead of one collage, which is what keeps characters
consistent. Seedance 2.5 and Kling 3.0 Pro unlocked, with Seedance 2 Mini, 480p
2.5, Kling 3.0 and MiniMax H3 available by choice. Roughly: one clip on Kling 3.0
Pro, three on Seedance 2 Mini, six on Grok. A full clip on Seedance 2.5 · 720p
does not fit — 3400 credits buys 20 of its 30 scenes.

**STUDIO · 10500 credits.** Everything, including Seedance 2.0. Two full clips on
Seedance 2.5, five on Kling 3.0 Pro, ten on Seedance 2 Mini.

## Two things that catch people out

**Re-animating is charged again.** Redrawing frames is free; asking a paid engine
for a second video of the same scene is a second purchase at the same price. On
Seedance 2.5 that is 152 credits for one retry. Settle the frames first.

**The plan decides the model, the scene decides the family.** Picking "Seedance"
on a scene gives you whichever Seedance your plan runs — 2 Mini on PRO, 2.5 on
PRO MAX. If a scene silently comes out cheaper than you expected, that is the
studio declining to hand a free account a flagship engine, not a bug.
