---
title: "First and Last Frame: The Control Trick Behind Every Clip That Holds Together"
description: "Give the model where a shot starts and where it ends, and it has to arrive. Here is why first-and-last-frame generation is the difference between 30 pretty clips and one music video."
slug: "first-and-last-frame"
lang: "en"
translationKey: "firstlast"
date: "2026-08-21"
updated: "2026-08-21"
cover: "/img/shots/step-frames.jpg"
tags: ["technique", "workflow", "engines"]
---

# First and Last Frame

There is a specific moment where AI music videos fall apart, and it is not the one people expect. It is not a bad face or a broken hand. It is the cut. Thirty individually decent six-second clips, stitched to a song, that feel like thirty clips instead of one video.

The fix is a feature most people skip past in the engine dropdown: **generating a scene from two frames instead of one**.

## Three ways to ask a model for a shot

**Text to video.** You describe the shot, the model invents everything, including the composition you had in your head and did not get. Cheapest to write, most expensive to iterate on: every rejection costs a full video generation.

**Image to video (first frame only).** You draw the opening frame and the model animates away from it. Much better — you control the composition, the character, the palette. But you control only the *departure*. Where the shot lands is the model's decision, and it will be a different decision every run.

**First and last frame.** You give the model both ends. It generates the motion between them, and it has to arrive at your second picture. Technically the reference images are encoded and the model interpolates a temporally consistent path from state A to state B; practically, it means a shot has a destination.

That third mode is what our pipeline is built on, and it is why every scene in the studio has two frames on its card, not one.

## Why the destination is the whole game in a music video

A single AI clip only needs to look good. A music video needs 30 shots that behave like they were filmed by the same person on the same day. Four things follow directly from controlling the last frame:

**1. The cut lands where the song wants it.** You can put the end of a shot on the downbeat by drawing the frame that should be on the downbeat. With first-frame-only generation, the shot ends wherever the model runs out of seconds, and you cut around the model instead of the music.

**2. Continuity across the cut becomes free.** If scene 7 ends on a face turned to camera, scene 8 can open on the same face turned to camera. The two frames are drawn from the same character reference, so the join reads as a cut rather than a jump. Chain that across a song and the video becomes a video.

**3. Motion becomes a decision, not a lottery.** "Camera pushes in past the doorway" is a prompt and the model may or may not take it. Frame A wide at the doorway plus frame B close on the face inside is a *specification*. The push-in happens because there is no other way to get from A to B.

**4. Rejections get cheap.** If the destination frame is wrong, you regenerate one image, not a video. In our pricing that is $0.09 instead of $1.98, and it is the single biggest lever on the cost of finishing a clip ([full cost breakdown](/blog/ai-music-video-cost/)).

## What it cannot do

This is where guides usually oversell, so, plainly: **first-and-last-frame is interpolation, not teleportation.**

The model builds a plausible path between your two pictures. If a plausible path exists, you get beautiful controlled motion. If it does not, you get mush — a morph where the subject melts through an impossible in-between.

The rule that comes out of running this at volume: **change one thing per scene.**

| Delta between frames | Result |
|---|---|
| Camera moves, subject holds | Excellent — the cleanest push-ins, pans and pull-backs you will get |
| Subject moves, camera holds | Very good — a turn, a step, a head lift, a hand raised |
| Light or weather shifts | Good — a lamp coming on, sun dropping, rain starting |
| Subject **and** camera **and** location all change | Mush. This is two scenes, not one |
| Different character in frame B | Mush. Always |

If you catch yourself drawing a second frame that is a whole new idea, you have found a cut. Split it into two scenes. Six seconds is enough for exactly one idea, which is also true of editing in general and has been since long before any of this existed.

## Which engines actually support it

Not all of them, and the ones that do wire it up differently.

| Engine | Last frame | How it takes it |
|---|---|---|
| Seedance 2 Mini / 2.0 / 2.5 | yes | `first_frame_url` + `last_frame_url` |
| MiniMax H3 | yes | `first_frame_url` + `last_frame_url` |
| Kling 3.0 / 3.0 Pro | yes | array of image URLs, first and last in order |
| Grok (our free gateway) | **no** | animates the first frame only |

The practical consequence for the free plan is worth stating up front rather than in a footnote: on Grok you get a real clip, but the shots animate away from your frame rather than travelling to a destination. Cuts are looser, and motion is suggested rather than specified. It is the right tool to test whether an idea works — and it is exactly why the free plan exists ([engine-by-engine comparison](/blog/seedance-vs-kling-vs-grok/)).

## The working method

This is the loop the studio runs, and it works the same way if you are assembling it by hand out of separate tools.

**Storyboard before pixels.** Cut the song into scenes with timecodes first. A shot rejected on a sketch sheet costs nothing; a shot rejected after it has been animated costs a full generation. Our step 2 draws the entire storyboard as one sheet for exactly this reason — you can see whether the story holds before spending anything on it.

**Lock the character once.** A face that has to survive 30 scenes cannot be re-described 30 times in prose; it drifts. It has to come in as reference images — a model sheet with several angles, plus separate attributes (the helmet, the glasses, the jacket) that get pulled in by name when a shot needs them. Modern image models take up to eight separate references, and that is what keeps scene 27 recognisable as the person from scene 3.

**Draw both frames from the same reference.** Frame A and frame B are the same character in the same place, one moment apart. Generate them from the same references and the same style, and the interpolation has almost nothing to fight.

**Keep the delta small, then animate.** One change. Then check the result and, if it is wrong, ask which of the two frames caused it — usually it is B, and usually it is because B was too ambitious.

**Chain the ends.** When you want two scenes to read as continuous, reuse the last frame of one as the starting point of the next.

## Try it on a real song

Every scene in the studio is a card with a first frame, a last frame, a redraw button on each, and a per-action credit price shown before you press anything. The free plan gives you a complete three-minute clip — 30 scenes — with no signup and no card, which is enough to feel the difference between a shot that has a destination and one that does not.

[Open the studio →](/) · [How the four steps work](/#ld-how) · [Plans and prices](/#ld-pricing)

**Related:** [What an AI music video actually costs](/blog/ai-music-video-cost/) · [Seedance vs Kling vs Grok](/blog/seedance-vs-kling-vs-grok/) · [Make a clip from your own track for free](/blog/make-music-video-from-your-track-free/)
