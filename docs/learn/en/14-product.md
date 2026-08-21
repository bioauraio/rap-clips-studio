---
title: "Product shots: the five frames of a listing"
description: "Hero shot, in the hand, texture macro, product in a life and the line-up. What makes a product frame look expensive, why the background sells more than the object, and where the engine fails on gloss."
slug: product
translationKey: product
lang: en
level: 3
access: free
minutes: 6
cover: /img/shots/step-frames.jpg
tags: [product, mockup, production]
mode: mockup
pack: product_kit
styles: [cinema]
date: 2026-08-22
updated: 2026-08-22
---

# Product shots: the five frames of a listing

A product frame differs from everything else in the studio in that it has an
objective test: it either sells or it does not. Beauty here is a function, not an
opinion.

Five frames cover a marketplace listing completely. More is unnecessary, fewer is a
hole.

## The five frames

| # | Frame | Answers |
|---|---|---|
| 1 | **Hero shot** | what is it |
| 2 | **In the hand** | how big is it |
| 3 | **Texture macro** | what is it made of |
| 4 | **Product in a life** | why do I want it |
| 5 | **The line-up** | what versions are there |

The order is not arbitrary: it is the order in which a buyer asks these questions,
silently and one at a time. A skipped question is an abandoned listing.

## Hero shot: one light, one surface

The whole device is a large soft source from the upper left and a subtle rim light
behind. Nothing else.

The beginner's mistake is lighting the object from every side so "everything is
visible". An evenly lit object looks flat and cheap. Volume comes from **shadow**,
and shadow only appears when there is one source.

The second decision is the **surface**. It accounts for half the result: wet stone,
rough paper, black glass. "White background" is not a surface, it is the absence of
a decision, and the model will fill it with whatever it likes.

## Macro: the cheapest expensive frame

Texture macro is the one card in the catalogue that is simultaneously the cheapest to
produce and the best-looking.

The reason is technical: at that scale there is nothing for the engine to get wrong.
No anatomy, no face, no perspective — just material under raking light. It comes out
right on the first attempt nearly always, free engines included.

If credits are short and the listing has to be closed, start here.

## Product in a life sells better than the hero shot

An object on white is a catalogue. An object on a windowsill among ordinary clutter
is a reason to want it.

The device explicitly says: **the object sharp, everything around it slightly soft**.
That makes the clutter work for us — it reads as life rather than mess, and it hides
whatever the engine rendered unconvincingly.

## Where the engine fails

**Gloss and reflections.** In the reflection of a polished surface the model
regularly draws things that are not in the scene. The "Gloss and reflection" device
(PRO) avoids this by keeping the scene nearly black with a single highlight — there
is simply nothing to reflect.

**Text on packaging.** Models spell badly and no prompt fixes it. Every product card
of ours says `no text` explicitly. If text is needed it goes on top of the finished
frame, it is not generated.

**The line-up.** Five identical objects in a row is the task where the model almost
always makes them slightly different. The cure is demanding identical height,
identical light and identical spacing in the prompt; if it still drifts, shoot the
line-up as three objects rather than seven.

## Steps

1. Create a project in **mockup** mode. The **Cinema** style comes with the pack: it
   has live light and staged composition, which is what is wanted here.
2. Apply the pack to five scenes.
3. Fill the slots: **product**, **surface**, **accent colour**, **location**.
   Describe the product by material and shape — "matte black glass bottle", not "our
   product".
4. Generate frames. Start with the macro — it shows whether the material was
   understood.
5. Get the hero shot right, then the other three.

## What to look at

**The shadow under the object.** Without it the frame looks like a collage. If it is
the same under all five items in a line-up, the light was understood correctly.

**One accent colour.** The five frames of a listing must look like one shoot. Keep
the accent identical in all five slots.

**Sharpness in the macro.** A correct macro is sharp in one band and soft everywhere
else. A fully sharp macro means the model did not understand the scale.

## The usual mistakes

**"White background."** Not a surface. Name the material.

**Several light sources.** Flat and cheap.

**Text in the prompt.** Do not put a brand name in the prompt — you will get a
handful of letters.

**Different accents across the five frames.** The listing falls apart into five
photographs that do not know each other.

**Skipping "in the hand".** The most common omission and the most expensive: without
it the buyer does not know the size and goes to the reviews to find out.

## The artefact

**Pack "Product card kit"** (`product_kit`) — hero shot, in the hand, texture macro,
product in a life, the line-up. The **Cinema** style is applied with it. Slots:
product, surface, accent colour, location. PRO plan: three of the five cards are open
for free, two are gated.

---

Next: **[Teardown: why that clip travelled](15-teardown.md)** — the teardowns level.
