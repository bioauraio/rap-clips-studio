---
title: "Shot devices: how one scene is filmed"
description: "The shot library is the third layer after styles and story frames. What the frame pair means, why a device here is the difference between the first and last frame, and the six shots every clip is built from."
slug: shots
translationKey: shots
lang: en
level: 1
access: free
minutes: 7
cover: /img/guide/board.png
tags: [shots, craft, camera]
mode: clip
pack: basic_shots
date: 2026-08-22
updated: 2026-08-22
---

# Shot devices: how one scene is filmed

The studio has three layers and they do not replace each other.

| Layer | Answers | Where it lives |
|---|---|---|
| **Style** | what the whole clip looks like | Prompts section, Styles tab |
| **Story frame** | what we are filming at all | same section, Frames tab |
| **Shot device** | how **one** scene is filmed | Devices tab |

A style and a story frame are set once per track. A device lands on one scene card
and touches nothing else in the project. That is why there are fifty-six devices
and only fifteen styles.

## A device is the difference between two frames

A scene here has two image prompts: **prompt** (the first frame) and **last frame
prompt**. A paid engine builds the video between them. So motion is not defined by
a camera path — it is defined by **how the last frame differs from the first**.

This is worth spelling out, because every other prompt bank on the internet works
the other way. There a device is a line like "drone orbit, 8 metre radius, 84
degrees". That line does not fit our frame pair at all: we do not have a minute of
flight, we have "here is the start, here is the end, get there".

So a push-in looks like this here:

- **first frame:** medium shot, character from the waist up, air above the head;
- **last frame:** same character, same light, same clothes — but only head and
  shoulders, filling two thirds of the frame;
- **motion:** camera travels on one axis, subject stays put.

Three fields instead of one trajectory. In exchange it works on any engine that
takes a first and last frame, and does not depend on whether the model knows the
word "dolly".

## There is no style inside a device

The device texts contain no words about film, anime, grade or grain. That is
deliberate: the pipeline supplies the style, and if the device supplied one too,
the prompt would carry two different worlds and the model would pick between them
on its own.

Practical consequence: **the same device works identically on Ghibli and on VHS.**
You can change the style of the whole track without touching a single scene.

## What Apply actually does

A device card has slots: `{character}`, `{location}`, `{time of day}` and so on.
Fill them, press **Apply**, and five fields land on the scene card at once — first
frame prompt, last frame prompt, motion prompt, shot size and camera move.

This is free. A device is text, and text costs no credits in this studio. You only
pay when you press **Generate frames**.

An unfilled slot substitutes the example from the dictionary. That is better than
leaving `{character}` in the prompt: models read curly braces literally and draw
the braces.

## The six shots every clip is built from

Below is the pack that applies in one click. It exists to cure the single most
common storyboard disease.

**The disease:** all thirty scenes are medium shots. The clip reads flat and you
cannot see why by eye — every individual frame is fine.

**The cure:** five different shot sizes across the first six scenes.

| # | Device | Shot size | What it does |
|---|---|---|---|
| 1 | City establishing shot | establishing | says where we are, once |
| 2 | Push in on the face | medium → close-up | a moment of attention |
| 3 | Cut-in detail | extreme close-up | punctuation |
| 4 | Locked-off frame | wide | somewhere to breathe |
| 5 | Walking alongside | medium | the sense the track is going somewhere |
| 6 | Pull back to reveal | close-up → wide | scale in a single scene |

All six are open on the free plan.

### Steps

1. Build the storyboard as usual — **Generate storyboard**.
2. Open the lesson and press **Apply pack**. It lands on the first six scenes.
3. Fill three slots once for the whole pack: **character**, **location**, **time of
   day**. The same character in all six — otherwise the pack does not assemble into
   a clip, it scatters into six postcards.
4. Walk the strip and fix the text where a device is not about that lyric. A device
   is a starting point, not a verdict.
5. Only now press **Generate frames**.

### What to look at

Open the **contact sheet** after the frames are generated. Six thumbnails in a row
should be obviously different sizes: city, face, fingernail, room, stride, whole
room. If everything on the sheet looks equally close, the devices did not land —
check that you pressed **Save frame** before regenerating.

### The usual mistakes

**Slots filled differently in different scenes.** "Guy in a hoodie" in scene one
and "the hero" in scene four are two different people to the model. Fill the pack
once.

**Applying the pack after the frames are already drawn.** The text is replaced, the
pictures are not, and it looks like the button is broken. Apply before generating,
or press **Regenerate frames** straight after — that is free.

**Putting a push-in on the cheap engine.** Grok animates only the first frame: it
has nowhere to put the difference between the two. The device card says so —
"needs an engine with first and last frame". Devices without that mark work on
Grok.

**Taking all six and stopping there.** The pack covers six scenes out of thirty.
After that it is the same five shot sizes on rotation, by hand.

## The artefact

**Pack "Six shots every clip is built from"** (`basic_shots`) — six cards, applied
in one click to the first six scenes. Slots: character, location, time of day.
Free plan.

---

Next: **[Light: five sources and what each one costs](09-light.md)** — the one
decision that changes a frame more than shot size does.
