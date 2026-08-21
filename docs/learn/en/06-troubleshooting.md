---
title: "It came out bad: the usual causes"
description: "Faces that drift, motion that does nothing, a clip that reads flat, and credits gone with nothing to show. Symptom, cause, fix."
slug: troubleshooting
translationKey: troubleshooting
lang: en
level: 2
access: free
minutes: 6
cover: /img/shots/step-frames.jpg
tags: [troubleshooting, craft]
date: 2026-08-21
updated: 2026-08-21
---

# It came out bad: the usual causes

Almost every disappointing first clip has the same three or four problems in it,
and almost all of them are fixed at the frame stage, where redrawing is free.

## The picture

### The character is a different person every scene

The most common one, and it is usually the description.

If **Personality and looks** is written in adjectives — *charismatic, iconic,
stylish* — there is nothing for the model to hold constant. Rewrite it as features:
build, hair, wardrobe, one distinguishing detail. Then upload two or three
**+ reference photo** images, and only then **Generate model sheet** so the
turnaround is built from your person rather than an invented one.

Second cause: the character is not actually toggled on in the scene. Each scene
card has **characters in the scene** — check that the right ones are lit up.

### Their jacket keeps turning into a different jacket

Signature objects belong in **Attributes**, not in the character description. Give
the jacket its own attribute with its own photo, then switch it on in the scenes
where it appears.

### The frame does not look like the style I picked

Two possibilities.

The scenes were generated *before* you changed the style, so the scene text is
still describing the old look. Press **Generate storyboard** again, then redraw
the frames.

Or you blended three presets that each specify a complete world, and they are
taking turns. Drop to one or two. See
[Styles: picking one, and blending three](01-styles.md).

### Text in the frame is gibberish

Frame generators are bad at letters and always have been. Do not plan a clip
around readable signage, lyrics burned into the frame, or a logo. If you need
text, make it a separate title-card scene with a simple background, or add it
afterwards in whatever you edit with.

### The composition is wrong and I cannot describe why

Stop writing prompt. Attach a reference image with **+ ref** on the scene card —
composition, light, the vibe of the frame — and redraw. One picture settles what
three paragraphs cannot.

## The motion

### Nothing really moves

If you are on the free plan, this is expected: Grok animates the **first frame**
and invents the rest, and what it invents is usually small. The fix is not a
better prompt, it is an engine that interpolates between your first and last
frame. See [Video engines](04-engines.md).

On a paid engine, the usual cause is that the first and last frames are nearly
identical. The engine has nowhere to travel. Redraw the last frame with an actual
difference in it — the character has crossed the room, the camera has pulled back,
the light has changed.

### The motion is chaos: things morph and melt

The opposite problem. Your first and last frames are in different rooms, or the
character changed clothes between them, and the engine is trying to get from one
to the other in six seconds.

Press **⟳ last** and redraw the last frame as the *same shot, later* — not as a
different shot. Free.

### The camera does something I did not ask for

**camera move** is free text and it is read literally. *slow push-in* works.
Leaving it empty lets the engine choose, and it usually chooses drift. Filling it
in is the cheapest control you have over motion.

## The edit

### Every frame is good and the clip is boring

Look at the sketch sheet — **Generate sheet**, **Open large** — and check the
shot sizes. A flat clip is almost always thirty medium shots in a row.

Vary them: an establishing shot to open, close-ups on the lines that matter, one
extreme close-up as a punctuation mark. Changing **shot size** and redrawing costs
nothing.

The other half of the same problem: no punch. The most-watched clips in this genre
put **one clear idea in each frame** — readable in a second, without sound. If you
cannot say what a scene is *of* in five words, it is probably a scene that should
be deleted.

### The cuts stutter

Two neighbouring scenes with almost the same framing read as an error rather than
a cut. Change one of them, or delete it.

### The clip ends before the song does

The assembly stops at whichever runs out first, video or audio. You have approved
fewer scenes than the track needs. Check the timecode on the last scene against
the length of the track, animate the missing scenes, reassemble.

### Black bars appear on some scenes

That scene came back from the engine in the wrong shape and was padded rather than
cropped. Re-animate that one scene.

## The money

### Credits are gone and there is no clip

Nearly always one of three things.

**You animated before fixing the frames.** Redrawing frames is free; re-animating
is charged at the engine's full video price. Get the frames right first, then
animate once.

**You pressed ⚡ One-click clip on a tight balance.** It charges for the entire
pipeline up front and approves every scene automatically, which means you never
get the free redraw pass.

**You re-animated repeatedly to fix something that was a frame problem.** If the
scene comes back wrong three times in a row, the frame is wrong, not the engine.

### "Not enough credits" halfway through the run

On the free plan, 120 credits is exactly one three-minute clip at 4 credits a
scene, with nothing spare. A generated character model sheet (2) and a storyboard
sheet (2) are enough to leave you a scene short.

Either use a shorter track for the free run — two minutes is 20 scenes, 80 credits
— or skip the two optional images. Full arithmetic in
[Your first clip](00-first-clip.md).

## Errors

**"⚡ One-click clip" refuses to start.** It needs three things: audio on the
track, a style picked, and at least one named character in the project. The dialog
tells you which one is missing.

**A scene sits on an error.** Open it and try again — most failures are the engine
timing out, not your input. If it fails repeatedly, check that the scene actually
has a first-frame prompt; an empty prompt is rejected.

**"Generate the frames of this scene first."** Video is built from the frames.
Draw them, then animate.

**The queue looks stuck.** It runs one scene at a time on purpose. You can close
the tab — progress is kept server-side and will be there when you come back.
