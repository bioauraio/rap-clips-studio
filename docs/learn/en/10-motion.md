---
title: "Motion: what the engine will not ruin"
description: "Why video comes out dead or smeared, and the single rule that fixes both — one movement per scene. Five scenes that come out clean even on the cheap engine."
slug: motion
translationKey: motion
lang: en
level: 2
access: free
minutes: 6
cover: /img/guide/anim.png
tags: [motion, engines, craft]
mode: clip
pack: motion_basics
date: 2026-08-22
updated: 2026-08-22
---

# Motion: what the engine will not ruin

Two complaints about video sound like opposites and have the same cause.

**"Nothing moves."** The motion prompt describes a mood, not an action.
`atmospheric, cinematic, dynamic` is zero instructions. The engine has nothing to
do and honestly does almost nothing.

**"Everything smeared."** Three actions were put in one scene: the character turns,
the camera travels, a car passes behind them. The engine tries all three and
delivers none.

One rule covers both: **one movement per scene.**

## Who is moving

Before writing the motion prompt, answer one question — what exactly travels:

- **the camera, subject still** — push in, pull back, tracking alongside;
- **the subject, camera still** — a turn, a run at the lens, a jump;
- **neither, the environment moves** — cloth in wind, rain, headlights, a crowd.

The third option is the most underrated. A scene where neither the camera nor the
person moves is still alive — and it never falls apart, because the engine never
has to preserve anatomy through movement.

## Five scenes that do not fall apart

| Device | What moves | Why it comes out clean |
|---|---|---|
| **The turn** | subject | one body part rotates |
| **Running at the camera** | subject | movement on one axis, no rotation |
| **Cloth in the wind** | environment | fabric is easy, the face is untouched |
| **The pass-by** | environment | one large object crosses, everything else is static |
| **Hands, no face** | subject | there is no face in frame to get wrong |

All five are open on the free plan. Four of the five are deliberately built so the
engine gets exactly one job.

### A note on "Hands, no face"

This is the most underrated card in the catalogue. Every clip has scenes where the
face drifts, and they are usually fixed by regenerating five times. Hands cannot
drift: there is no face in the frame to ruin. The emotion survives anyway — two
hands doing one small thing read more precisely than a bad face.

### A note on "The pass-by"

A car crosses the frame and briefly hides everything behind it. In the last frame
there is a person standing where nobody was in the first. It is a free wipe: a
change of location inside one scene, with no cut and no editing software.

## Steps

1. Walk the storyboard and find the scenes whose **frame animation prompt** field
   contains adjectives. Those are your future dead frames.
2. Decide for each one what moves: camera, subject or environment.
3. Apply the pack to five scenes and fill the slots: **character**, **location**,
   **outfit**. The outfit matters — it is what carries recognition between scenes.
4. Rewrite the motion on the remaining scenes on the same principle: one verb.
5. **Generate frames**, get the pictures right, and only then the video.

## What to look at

Check the video of the **first** scene before animating the other twenty-nine.
Regenerating video is the only action in the studio that costs credits every single
time. The order "one scene → look → the rest" saves more than any saving on engines.

Watch the edges of the frame. If a hand has drifted or the face has become a
different person, the problem is not the animation — it is that too much is moving
in that scene.

## The usual mistakes

**Adjectives instead of verbs.** `dynamic, energetic, cinematic motion` carries no
information. `The camera moves forward on one axis, the subject stays still` is an
instruction.

**Two verbs in one scene.** "Turns and walks" will be read as neither. Split it
into two scenes — they will sit better against the music anyway.

**A push-in and a turn at once.** The classic. The camera travels forward while the
character rotates, and both smear. Keep one.

**Regenerating video instead of regenerating frames.** If the problem is in the
picture, video will not fix it but will charge for it. Redrawing frames is free;
regenerating video is not.

**Twelve-second scenes.** The longer the scene, the more the engine has to invent.
A long scene needs **mid-frames**, otherwise it picks the shortest, dullest path
between the start and the end.

## The artefact

**Pack "Motion that survives a cheap engine"** (`motion_basics`) — five scenes built
so the engine gets one job: the turn, running at the camera, cloth in the wind, the
pass-by, hands only. Slots: character, location, outfit. Free plan.

---

Next: **[Editing: half the clip lives at the cut](11-cuts.md)**.
