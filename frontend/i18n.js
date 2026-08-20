// ══════════════════════════ lolq.ai — словарь интерфейса ══════════════════════════
// EN — основной язык сервиса, RU — второй. ВСЕ пользовательские строки живут
// здесь и только здесь: разметка держит ключи (data-i18n="путь.в.словаре"),
// код зовёт t("путь"). Перевод на третий язык = ещё один блок в I18N.
//
// Как этим пользоваться:
//   t("track.saveTrack")                 → строка текущего языка
//   t("track.clipTitle", {a: 3, b: 6})   → подстановка {a}/{b}
//   tRaw("styles.ghibli")                → объект/массив как есть (для рендера списков)
//   applyI18n(root)                      → проставляет тексты по data-i18n* внутри root
//   setLang("ru")                        → переключение + перерисовка (хуки регистрирует app.js)
//
// Атрибуты разметки: data-i18n (текст), -html (разметка), -ph (placeholder),
// -title, -alt, -aria (aria-label), -content (meta), -value.

const LANGS = ["en", "ru"];
const LANG_KEY = "lolq_lang";

const I18N = {
  // ═══════════════════════════════ ENGLISH ═══════════════════════════════
  en: {
    common: {
      save: "Save",
      saving: "saving…",
      cancel: "Cancel",
      create: "Create",
      close: "Close",
      del: "Delete",
      delTitle: "delete",
      copy: "copy",
      copied: "copied",
      copyManual: "copy this by hand",
      loading: "loading…",
      generate: "Generate",
      generating: "generating…",
      loadFail: "could not load",
      madeWith: "made with :333",
      moveUp: "move up",
      moveDown: "move down",
      prev: "previous",
      next: "next",
    },

    lang: {
      aria: "Interface language",
      en: "EN",
      ru: "RU",
      switchTitle: "Switch interface language",
    },

    meta: {
      description: "lolq.ai — an AI music video studio for your own tracks: upload a song, get a vertical 9:16 clip with your own characters.",
    },

    status: {
      queued: "queued…",
      running: "generating…",
      error: "error",
      done: "done",
    },

    auth: {
      loginPh: "login (empty — owner sign-in)",
      passwordPh: "password",
      submit: "sign in",
      fail: "wrong login or password",
    },

    top: {
      brandTitle: "lolq.ai — music video studio",
      coverTitle: "project cover — click to replace",
      projectNamePh: "clip name",
      newProject: "+ project",
      pointsTitle: "credits left",
      pointsUnit: "credits",
      account: "Account",
      accountTitle: "plan, credits, affiliate",
      saveAccount: "Save account",
      logout: "log out",
      kindAlbum: "album",
      kindSingle: "single",
      planPro: "{plan} · Seedance",
      planFree: "FREE · Grok",
      planProTitle: "Seedance unlocked: motion between the first and the last frame",
      planFreeTitle: "free plan: video via Grok, Seedance opens on a paid plan",
    },

    story: {
      title: "Character & story",
      bible: "Character bible",
      bibleTitle: "looks and personality — one for every track",
      biblePh: "Leave it empty and it will be written from your first tracks",
      arc: "Story arc",
      arcPh: "Shows up after generation",
      gen: "Write the story from all tracks",
    },

    chars: {
      title: "Album characters",
      titleHint: "name + personality + reference photos: they carry across the whole album, frame prompts use the names and the looks are always pulled in",
      add: "+ add character",
    },

    tracks: {
      title: "Tracks",
      add: "+ add track",
      titlePh: "Track title",
      styleLabel: "Clip style (1–3 presets, the first one is the base)",
      lyricsPh: "Lyrics (optional — an instrumental works too: then the comment and the characters carry the idea)",
      commentPh: "Comment: what you meant, the context",
      submit: "Add track",
    },

    stages: {
      setup: "Setup",
      board: "Storyboard",
      anim: "Animation",
    },

    stylePicker: {
      descSummary: "what the picked styles look like",
      none: "no style picked",
      base: "base",
      plus: "plus",
      custom: "(this track has its own custom style — picking a preset will replace it)",
    },

    track: {
      coverTitle: "track cover — click to replace",
      titlePh: "Title",
      supergen: "⚡ One-click clip",
      supergenBusy: "⚡ making everything…",
      supergenTitle: "the whole pipeline in one button",
      delTitle: "delete",
      delConfirm: "Delete this track together with its storyboard?",
      styleLabel: "Clip style",
      styleLabelTitle: "1–3 presets as checkboxes: the first one you tick is the base of the blend",
      audio: "Audio",
      audioProfile: "audio profile",
      grain: "film grain over the whole clip",
      grainTitle: "16 mm ffmpeg grain over the assembled clip",
      nostory: "no story (random punch frames)",
      nostoryTitle: "the storyboard becomes independent random punch frames built from your comment — no story arc needed",
      comment: "Comment",
      commentPh: "what you meant, the context",
      lyrics: "Lyrics",
      lyricsPh: "Empty = pure beat: the comment and the characters carry the idea",
      saveTrack: "Save track",
      noteLabel: "Director's note for this track",
      genScenes: "Generate storyboard",
      genScenesTitle: "write the album story first — the “Character & story” panel above",
      scenesCount: "scenes: {n}",
      scenesDone: "done, scenes: {n}",
      sheetTitle: "Storyboard sheet",
      sheetEmpty: "no sheet yet",
      sheetOpen: "Open large",
      sheetOpenTitle: "open the sheet full screen",
      genSheet: "Generate sheet",
      redrawSheet: "Redraw sheet",
      sheetBusy: "drawing the sheet…",
      sliceSheet: "Split the sheet into frames",
      sliceTitle: "slice the sheet grid: every cell becomes the first frame of its scene",
      addScene: "+ add scene by hand",
      allFrames: "Generate frames for all scenes",
      allFramesN: "Frames for all scenes ({n})",
      allFramesBusy: "generating frames…",
      allFramesTitle: "the queue runs one scene at a time — you can close the tab, progress is kept",
      allFramesNote: "the queue runs one scene at a time",
      allVideos: "Animate all scenes",
      allVideosN: "Animate all scenes ({n})",
      allVideosBusy: "generating video…",
      allVideosTitle: "queue video for every scene that already has frames",
      allVideosConfirm: "Queue video for {n} scenes? This spends video engine credits.",
      animEmpty: "No video yet — generate it from the Storyboard stage: the Animate button on a scene card.",
      clipHead: "Finished clip of the track",
      clipTitle: "Finished clip — approved scenes: {a}/{b}",
      clipEmpty: "no clip yet",
      assemble: "Assemble clip",
      reassemble: "Reassemble",
      assembleBusy: "assembling the clip…",
      assembleTitle: "approve at least one scene (Animation stage)",
      autoAsm: "auto-assemble",
      autoAsmTitle: "every new scene video goes into the clip on its own and the clip is reassembled — no need to press the button",
      autoAsmOn: "on: new scene videos go into the clip",
      clipDone: "clip is ready",
      download: "download",
      finalScenes: "Video of every scene",
      finalEmpty: "no scene videos yet — they show up after the Animation stage",
    },

    scene: {
      pos: "Scene {n}",
      playTitle: "play this scene",
      editTitle: "edit scene",
      delTitle: "delete scene",
      delConfirm: "Delete this scene?",
      firstFrame: "first frame",
      lastFrame: "last frame",
      firstCap: "first",
      lastCap: "last",
      midThumb: "in-between frame {n}",
      refsTitle: "scene references: composition, light, vibe",
      refTitle: "scene reference — click to open the original",
      refDel: "remove reference",
      refAdd: "+ ref",
      refUploadTitle: "a sample image: composition, light, the vibe of the frame",
      charsTitle: "characters in the scene — click to toggle",
      prompt: "prompt",
      promptTitle: "first frame prompt",
      durationTitle: "seconds",
      shotSizeTitle: "shot size",
      shotSizeEmpty: "shot size —",
      shot: {
        "extreme close-up": "extreme close-up",
        "close-up": "close-up",
        medium: "medium",
        wide: "wide",
        establishing: "establishing",
      },
      cameraPh: "camera move",
      cameraTitle: "e.g. slow push-in",
      lyricPh: "lyric line",
      notePh: "what happens in the frame",
      motionPh: "motion prompt for the scene",
      motionLastPh: "last frame prompt",
      save: "Save scene",
      genFrames: "Generate frames",
      regenFrames: "Regenerate frames",
      framesBusy: "drawing frames…",
      genFirst: "⟳ first",
      genFirstTitle: "rebuild only the first frame — the last one stays",
      genLast: "⟳ last",
      genLastTitle: "rebuild only the last frame — the first one stays",
      midBtn: "+ in-between frames ({n})",
      midBusy: "in-between {a}/{b}…",
      midShort: "the scene is too short — it needs no in-between frames",
      midNoFrame: "generate the scene frames first",
      midTitle: "one frame between the first and the last, 1 credit each",
      providerTitle: "video engine",
      providerSeedance: "Seedance (2 frames)",
      providerGrok: "Grok (1 frame)",
      providerSeedanceShort: "Seedance",
      providerGrokShort: "Grok",
      genVideo: "Animate scene",
      regenVideo: "Re-animate",
      videoBusy: "generating…",
      videoNoFrame: "frame first",
      videoTitleNoFrame: "Generate the frames of this scene first — the video is built from the first and the last frame",
      videoTitle: "Bring the scene to life: first + last frame → video",
      approve: "use in clip",
      approveTitle: "approved scenes go into the final clip",
      approveNeedVideo: "generate the scene video first",
      audioTitle: "the slice of the track under this scene",
      cap: "Scene {n} · {time}",
      capApproved: " · in clip",
    },

    character: {
      namePh: "Character name",
      noName: "no name",
      openTitle: "click: the character's dossier",
      attrsN: "attributes: {n}",
      attrsNone: "no attributes",
      main: "main",
      delTitle: "delete",
      delConfirm: "Delete the character “{name}” together with the photos?",
      descLabel: "Personality and looks",
      descTitle: "goes into the prompts word for word",
      upload: "+ reference photo",
      genModel: "Generate model sheet",
      genModelTitle: "generate a four-angle turnaround from the description and the uploaded photos",
      photoDel: "delete photo",
      attrs: "Attributes",
      attrsTitle: "signature things: a hat, glasses, a quad bike…",
      attrAdd: "+ attribute",
      attrEditTitle: "click: edit",
      attrDelTitle: "delete attribute",
      attrPhotoAdd: "+ photo",
    },

    modal: {
      closeTitle: "close",

      saveAccount: {
        title: "Save account",
        lead: "A login and a password lock this account down: your projects and files stay with you on any device.",
        loginLabel: "Login",
        loginPh: "login",
        passLabel: "Password (6 characters or more)",
        passPh: "password",
        nameLabel: "Name (optional)",
        namePh: "what should we call you",
      },

      newProject: {
        title: "New project",
        nameLabel: "Name",
        namePh: "Project name",
        kindLabel: "Project type",
        album: "Album",
        albumNote: "several tracks",
        single: "Single",
        singleNote: "one track",
        coverLabel: "Cover (optional)",
        coverHint: "＋ pick a file (jpg / png / webp)",
        nameRequired: "type a name",
      },

      addChar: {
        title: "Add a character",
        tabNew: "New",
        tabLibrary: "From the library",
        nameLabel: "Character name",
        namePh: "e.g. Artem",
        nameRequired: "type a name",
        libLead: "A character from any project: the name, the description and the reference photos will be copied into this one.",
        libEmpty: "the library is empty so far",
        libHere: "already here",
      },

      attribute: {
        newTitle: "New attribute",
        editTitle: "Character attribute",
        nameLabel: "Name (e.g. red cap)",
        namePh: "what the thing is called",
        descLabel: "Description (optional — goes into the prompts)",
        nameRequired: "type a name",
        delTitle: "Delete the attribute?",
        delText: "The attribute “{name}” and all of its photos will be deleted.",
      },

      supergen: {
        title: "⚡ One-click clip",
        styleOk: "Clip style is picked",
        styleBad: "No clip style — pick a preset on the track card",
        charsOk: "Characters: {names}",
        charsBad: "The project has NO characters — add one or clone it from the library",
        ideaOk: "There is an idea (lyrics or a comment)",
        ideaBad: "No lyrics and no comment — write the idea of the clip in the comment field",
        info: "The story arc is optional: write your own in “Character & story” or leave it empty and it will be written for you. Everything after that runs on its own: story → storyboard → frames → video of every scene → the clip assembled with your track. Progress shows up on the track card. If the scenes were already generated in a different style, hit “Generate storyboard” again first.",
        go: "Let's go",
      },

      sheet: {
        title: "Storyboard sheet",
        full: "Actual size",
        fit: "Fit to screen",
        original: "open the original",
      },

      character: {
        title: "Character",
      },

      cells: {
        title: "Split the sheet into frames",
        hint: "Tick the cells you keep and pick a scene for each one. The other scenes stay untouched — you can regenerate them separately.",
        toScene: "into scene {n}",
        apply: "Apply the picked ones",
        nonePicked: "no cells picked",
        applied: "Frames placed: {n}",
      },

      model: {
        title: "Model sheet: {name}",
        someone: "character",
        withPhotos: "The first {n} photos of the character go in as reference — the face and the clothes will hold.",
        noPhotos: "No photos uploaded — the model sheet will be built from the description alone.",
        descLabel: "Description for the model sheet",
        descPh: "looks, clothes, attributes — the more detail the closer it lands",
        kindLabel: "Model sheet type",
        kind3d: "3D model (CG render)",
        kindReal: "Photoreal (studio)",
        kindAnime: "Anime (settei sheet)",
        busy: "generating… (up to 2 minutes)",
      },
    },

    account: {
      title: "Account",
      tabs: {
        account: "Account",
        plan: "Plan",
        ref: "Ambassador",
        payouts: "Payouts",
      },
      guest: "guest",
      noContacts: "account with no email and no login",
      statPlan: "plan",
      statUntil: "active until",
      statPoints: "credits",
      statProjects: "projects",
      logins: "Sign-in methods",
      password: "Password",
      yandex: "Yandex",
      autopayOff: "Turn off auto-renewal",
      autopayOffNote: "the plan keeps running until the end of the paid period",
      autopayOffBusy: "turning off…",
      autopayOffDone: "auto-renewal is off",
      autopayNote: "auto-renewal is off",
      choosePlan: "Choose a plan",
      renewPlan: "Renew the plan",
    },

    plan: {
      pointsLine: "{n} credits",
      current: "current",
      basic: "basic",
      pay: "Pay",
      promoLabel: "Promo code",
      promoPh: "ambassador code — a discount on your first payment",
      creating: "creating the payment…",
      payOff: "payments are not connected yet",
      payOffTitle: "payment processing is not set up yet — ping the owner",
      payOffNote: "Payment processing is not connected yet — the plans are shown, but you cannot pay.",
      noUrl: "the checkout returned no payment link",
    },

    ref: {
      joinLead: "Bring people in on your own link — and take a cut of what they pay.",
      joinDiscount: "your friend gets <b>{pct}%</b> off their first payment",
      joinReward: "you get <b>{pct}%</b> of every payment they make, not just the first one",
      joinPayout: "payouts to your own details from {sum}",
      join: "Become an ambassador",
      joining: "connecting…",
      codeLabel: "Your promo code",
      linkLabel: "Referral link",
      note: "Your friend gets {discount}% off their first payment, you get {reward}% of every payment they make.",
      statInvited: "invited",
      statBuyers: "paid",
      statAccrued: "earned",
      statPaid: "paid out",
      statAvailable: "available",
      turnover: "turnover of the people you invited — {sum}",
      reserved: ", in open requests — {sum}",
      eventsLabel: "Latest events",
      eventPayment: "payment {sum}",
      eventVisit: "came in on the link",
      eventsEmpty: "nothing yet — share your link",
      detailsLabel: "Payout details",
      detailsPh: "card, phone transfer, who to pay",
      detailsSave: "Save details",
      detailsSaved: "details saved",
      payoutLabel: "Request a payout",
      payoutPh: "amount in ₽",
      payoutBtn: "Request a payout",
      payoutNote: "Leave the amount empty and we request everything available. The minimum payout is {sum}, and the money goes into reserve the moment you ask.",
      payoutBusy: "sending the request…",
      payoutDone: "request accepted — the money will go to your details",
      myPayouts: "My requests",
    },

    payouts: {
      queue: "Queue",
      filterNew: "new",
      filterPaid: "paid",
      filterRejected: "rejected",
      filterAll: "all",
      empty: "no requests in this queue",
      noDetails: "no payout details given",
      markPaid: "Paid",
      markRejected: "Reject",
      statusNew: "in progress",
      statusPaid: "paid",
      statusRejected: "rejected",
    },

    styles: {
      ghibli: {
        label: "Hayao Miyazaki (warm anime)",
        desc: "Hand-drawn anime with watercolour backgrounds and cosy light — a frame out of Ghibli.",
      },
      pixar: {
        label: "3D cartoon (Pixar-style)",
        desc: "Glossy 3D animation: expressive characters, rich cinematic light.",
      },
      shinkai: {
        label: "Cinematic anime (Shinkai)",
        desc: "Modern anime with impossibly beautiful skies, lens flares and emotional gradients.",
      },
      cinema: {
        label: "Realism (film)",
        desc: "Photoreal cinema shot on film: honest light, skin texture, a little grain.",
      },
      flat2d: {
        label: "Flat 2D animation",
        desc: "Bright flat vector animation: simple shapes, bold outlines, poster-like compositions.",
      },
      noir: {
        label: "Noir comic",
        desc: "Black and white noir with one accent colour: deep shadows, rain, neon.",
      },
      longheads: {
        label: "Long heads (90s analog surrealism)",
        desc: "90s analog film: surreal long-headed characters living an ordinary street life.",
      },
      embroidery: {
        label: "Cardboard (thread embroidery)",
        desc: "The whole frame stitched in thread on cream felt and kraft board — handmade and warm.",
      },
      spike: {
        label: "SPIKE (Russian cine-surrealism, cameos)",
        desc: "Night-time Russian cine-surrealism: panel blocks, old Ladas, smoke and cartoon cameos played dead straight.",
      },
      munir: {
        label: "MUNIR (Gulf, flash, fisheye)",
        desc: "Gulf street photography with flash and a fisheye: rings pushed into the lens, a G63, Dobermans.",
      },
      fanuel: {
        label: "FANUEL (cinematic surrealism, fire)",
        desc: "Surreal fashion film: a lone figure in a suit inside impossible landscapes, fire everywhere.",
      },
      clay: {
        label: "Claymation (plasticine)",
        desc: "Plasticine stop-motion: fingerprints in the clay, miniature sets, warm practical light.",
      },
      punkrf: {
        label: "PUNKRF (found footage, Russian chaos)",
        desc: "Hyperreal “random video” of night-time Russia: dashcams and VHS, red neon, absurdity in the middle of traffic.",
      },
      dreamclad: {
        label: "DREAMCLAD (90s hood cinema)",
        desc: "90s hood cinema on film: grain, white tanks and bandanas, money, doves and crosses, icon-like frontal framing.",
      },
      katsumi: {
        label: "KATSUMI (found footage, surreal)",
        desc: "Hyperreal found footage: rats, monks and aliens doing ordinary human things dead straight, shot on a 90s camcorder with flash.",
      },
    },

    guide: {
      title: "How it works",
      chars: {
        title: "Characters with model sheets",
        text: "Set up a character: upload their photos or hit “Generate model sheet”. The face and the clothes hold across every frame, while the quad bike, the helmet and the glasses live as separate attributes.",
        alt: "Character card: description of the looks, reference photos and attributes",
      },
      setup: {
        title: "Track and style",
        text: "Upload the audio and pick 1–3 style presets — they blend into one look. Lyrics are optional: the comment carries the idea, and “no story” mode builds the clip out of random punch frames.",
        alt: "The Setup stage: style chips, track audio, comment and checkboxes",
      },
      board: {
        title: "Storyboard",
        text: "Claude cuts the track into scenes that follow the real dynamics of the audio. The sketch sheet can be split into frames selectively, any single frame can be redrawn on its own — only the first or only the last — and you can attach your own references to it.",
        alt: "The Storyboard stage: the sketch sheet and the strip of scene cards with thumbnails",
      },
      anim: {
        title: "Animation",
        text: "Every scene comes to life from its first and last frame (Seedance) or from the first one alone (Grok). One button queues every scene at once.",
        alt: "The Animation stage: scene videos marked “use in clip”",
      },
      ready: {
        title: "The finished clip",
        text: "Approved scenes are glued to your own audio. Film grain over the whole clip is a checkbox, and the finished file downloads.",
        alt: "The Final stage: the assembled clip and the grid of every scene video",
      },
    },

    errors: {
      generic: "something went wrong",
      network: "no connection to the server",
      unauthorized: "session expired — sign in again",
      codes: {
        network: "No connection to the server. Check the network and try again.",
        unauthorized: "Your session has expired — sign in again.",
        not_enough_points: "Not enough credits: this step costs {need}, you have {have}. Top up {short} more or upgrade your plan.",
        payments_disabled: "Payments are not connected yet.",
        unknown_plan: "Unknown plan.",
        unknown_pack: "Unknown credits pack.",
        stripe_failed: "Stripe did not take the payment. Try again in a minute.",
        yookassa_failed: "YooKassa did not take the payment. Try again in a minute.",
      },
    },

    landing: {
      nav: {
        skip: "Skip to content",
        how: "How it works",
        features: "Features",
        pricing: "Pricing",
        faq: "FAQ",
        music: "qlolmusic",
        signin: "Sign in",
        start: "Start free",
        open: "Open the studio",
        menuAria: "page sections",
      },

      hero: {
        eyebrow: "Music videos for your own tracks",
        title: "Drop a track, leave with a video",
        sub: "The studio cuts your song into scenes that follow its own dynamics, draws the frames in the style you pick and brings them to life. Vertical 9:16, characters that hold across scenes, frame-by-frame control. The first clip is free and needs no signup.",
        ctaStart: "Start free",
        ctaOpen: "Open the studio",
        ctaLogin: "I already have an account",
        trust: "no signup · 120 credits for the first clip · your files stay private",
        trustBack: "you are signed in · your projects and files are waiting in the studio",
        guideLink: "See what it looks like inside →",
        phoneCap: "0:26 · 9:16",
        proofMeta: "track “Bully” · 6 scenes · 26 seconds",
        proofCap: "The clip and every frame were made by the studio out of one uploaded beat — nothing was drawn by hand.",
        proofAltClip: "A frame from a finished clip: a character in a white helmet on a quad bike by a night kiosk",
        refBanner: "You came in on an invite {code} — {discount}% off your first payment",
      },

      how: {
        eyebrow: "How it works",
        title: "Four steps from a track to a finished clip",
        lead: "Not a single “hold on, magic is happening” screen: you see the result after every step, and any piece can be redone on its own.",
        steps: [
          {
            n: "01",
            title: "Track and style",
            text: "Upload an mp3 and tick up to three of the fifteen styles — they blend into one look, and the first one you pick becomes the base. The lyrics are optional: the comment field explains the context.",
            meta: "15 styles · blend up to three · an instrumental works too",
            img: "/img/shots/step-track.jpg", w: 875, h: 300,
            alt: "The track setup screen: style chips, audio, comment",
          },
          {
            n: "02",
            title: "Story and storyboard",
            text: "The model reads the length and the structure of the audio and cuts it into scenes with timecodes, shot sizes and camera moves. A sketch sheet of the whole storyboard is drawn next to it — you can see at a glance whether the story holds.",
            meta: "scenes with timecodes · sketch sheet · one story arc or random punch frames",
            img: "/img/shots/step-board.jpg", w: 700, h: 478,
            alt: "A storyboard sheet: a grid of six frames of the future clip",
          },
          {
            n: "03",
            title: "Scene frames",
            text: "Every scene gets its own first and last frame — the motion is built between them. Do not like a frame? That one frame is redrawn, not the whole scene, and you can attach your own reference for composition or light.",
            meta: "first / last frame · redraw one at a time · your own references",
            img: "/img/shots/step-frames.jpg", w: 875, h: 330,
            alt: "Scene cards with the first and the last frame and redraw buttons",
          },
          {
            n: "04",
            title: "The clip",
            text: "Approved scenes are glued to your own audio into one vertical clip. Film grain over the whole thing is a checkbox, and the finished file downloads.",
            meta: "9:16 · film grain · mp4 out",
            img: "/img/shots/step-clip.jpg", w: 875, h: 790,
            alt: "The finished clip screen: the assembled video and every scene video",
          },
        ],
      },

      features: {
        eyebrow: "What is inside",
        title: "Tools, not one “make it pretty” button",
        lead: "Everything that makes a clip different from a pile of random pictures: characters that hold, one style throughout, and a way into any single frame.",
        items: [
          {
            title: "Characters with a model sheet and attributes",
            text: "Set a character up once: a description, photos, or a generated four-angle model sheet. The face and the clothes hold across every scene of the album, while the helmet, the glasses and the quad bike live as separate attributes and get pulled in by name.",
            img: "/img/shots/feat-chars.jpg", w: 875, h: 240,
            alt: "Character card: description, reference photos and attributes",
            wide: true,
          },
          { title: "15 styles and blends",
            text: "From warm hand-drawn anime and Shinkai to found footage and claymation. Blend up to three and the look comes out yours." },
          { title: "A storyboard that follows the beat",
            text: "Scenes are cut by the length and the structure of the audio, not by an even grid: the beat has its own timecodes." },
          { title: "Frame-by-frame redraws",
            text: "One frame off? Only that frame is rebuilt: the first, the last or the in-between ones. The rest of the scene stays exactly as it was." },
          { title: "16 mm film grain",
            text: "Grain is laid over the assembled clip in one pass, not frame by frame, so the picture does not flicker." },
          { title: "Vertical 9:16 from the start",
            text: "Frames, motion and assembly are vertical from step one: the clip goes into Reels and Shorts with no reframing and no bars." },
          { title: "Publishing to Instagram",
            text: "The finished clip goes to Reels straight from the studio — no downloading the file to your phone first.",
            tag: "in progress" },
        ],
      },

      pricing: {
        eyebrow: "Pricing",
        title: "You pay for the engines, not for the interface",
        lead: "Credits are the single unit of work: a scene costs 4 on Grok, 10 on Seedance 2.0, 16 on Seedance 2.5 and Kling. A three-minute clip is around 30 scenes.",
        month: "Monthly",
        year: "Yearly",
        yearSave: "−20%",
        free: "$0",
        forever: "forever",
        perMonth: "/ mo",
        yearHint: "or {mo} a month billed yearly",
        yearNote: "{total} a year · billed once",
        pointsLine: "{points} credits a month",
        clipsLine: "≈ {clips} {word} of 3 minutes",
        clipsLineOne: "≈ 1 clip of 3 minutes",
        clipWord: ["clip", "clips", "clips"],
        cta: "Choose {plan}",
        ctaFree: "Start free",
        current: "your plan",
        creating: "creating the payment…",
        payOff: "payments coming soon",
        payOffNote: "Payment processing is not connected yet: the plans are shown honestly, but the pay button does not work. The studio and the free plan work right now.",
        noUrl: "the checkout returned no payment link",
        plans: {
          free: {
            title: "FREE",
            note: "One full three-minute clip, on us.",
            engine: "grok",
            features: [
              "120 credits — enough for a whole clip",
              "Grok: animates the first frame of every scene",
              "Story, storyboard, characters and assembly",
            ],
          },
          pro: {
            title: "PRO",
            note: "Seedance 2.0: motion between the first and the last frame.",
            engine: "seedance",
            features: [
              "700 credits every month",
              "Seedance 2.0 — honest motion built from two frames",
              "Unused credits roll over, up to two months' worth",
            ],
          },
          pro_max: {
            title: "PRO MAX",
            note: "Seedance 2.5 and Kling — the ones that actually look good.",
            engine: "top",
            badge: "Most popular",
            hi: true,
            features: [
              "2,400 credits every month",
              "Seedance 2.5 and Kling unlocked",
              "Unused credits roll over, up to two months' worth",
            ],
          },
          studio: {
            title: "STUDIO",
            note: "Album-scale volume on any engine.",
            engine: "top",
            badge: "For labels",
            features: [
              "6,000 credits every month — about a dozen clips",
              "Every engine, Seedance 2.5 and Kling included",
              "Priority processing and direct support",
            ],
          },
        },
      },

      topup: {
        eyebrow: "Top up",
        title: "Out of credits halfway through the album? Top up without changing plan",
        lead: "A pack sits on top of your subscription. The bigger the pack, the cheaper the credit.",
        note: "Topped-up credits do not expire when the plan renews and do not count against the roll-over cap — you paid for them separately.",
        priceUnit: "per pack",
        decimalSep: ".",
        pointsUnit: "{points} credits",
        save: "−{pct}% per credit",
        clipsTop: "≈ {clips} {word} of 3 min on Seedance 2.5",
        clipsGrok: "or ≈ {clips} {word} on Grok",
        cta: "Buy {points} credits",
        creating: "creating the payment…",
      },

      partner: {
        eyebrow: "Affiliate program",
        title: "Bring your people, take a cut",
        lead: "An ambassador gets a promo code and a link. Everything is counted automatically, payouts go to the details in your account.",
        items: [
          "your friend gets <b>{discount}%</b> off their first payment",
          "you get <b>{reward}%</b> of every payment they make, not just the first one",
          "clicks and payments in your account, a payout request in one click",
        ],
        cta: "Become an ambassador",
        note: "It takes one click: an account is created for you if you do not have one yet.",
      },

      faq: {
        eyebrow: "FAQ",
        title: "What people usually ask",
        items: [
          { q: "Who owns the clip?",
            a: "You do. We make no claim on the video you create and we do not put your projects in a showcase without asking. The only limits come from the engines that draw the frames: they forbid deepfakes of real people without consent and outright banned content." },
          { q: "What about the music?",
            a: "You bring your own music: the studio neither writes it nor publishes it anywhere. The file sits in the private storage of your project and is used only to cut scenes by timecode and to glue the final clip." },
          { q: "How long does generation take?",
            a: "Frames of a scene take about a minute, the video of a scene from two to five minutes depending on the engine. A six-scene clip end to end takes 20–40 minutes. The queue is shared and single-threaded, so at peak hours a job may wait." },
          { q: "Can I use the clips commercially?",
            a: "Yes, on any plan including the free one: post them to Reels, Shorts, streaming platforms and ads. There is no separate commercial licence to buy." },
          { q: "What happens if I run out of credits?",
            a: "The generation simply does not start, and the service says plainly how many credits are needed and how many you have. Nothing already made is lost: top up a pack or wait for the plan to renew, and carry on from the same place." },
          { q: "Do unused credits expire?",
            a: "The monthly allowance of your plan rolls over to the next month, but not beyond two months' worth. Credits bought as a pack never expire." },
          { q: "How do I cancel the subscription?",
            a: "Account → “Account” → “Turn off auto-renewal”. No more charges, and the plan runs to the end of the period you already paid for — the credits for it stay yours." },
          { q: "Do I need to sign up?",
            a: "No. “Start free” creates an account with no form: you can work and walk away. A login and a password are added later with one button in the studio — the projects and files stay the same." },
        ],
      },

      footer: {
        about: "An AI music video studio for your own tracks. Runs in the browser: nothing to install.",
        cols: [
          { title: "Product", links: [
            { label: "How it works", href: "#ld-how" },
            { label: "Features", href: "#ld-features" },
            { label: "Studio guide", action: "guide" },
            { label: "Styles breakdown", href: "/report/styles.html" },
          ] },
          { title: "Payments", links: [
            { label: "Pricing", href: "#ld-pricing" },
            { label: "Top up credits", href: "#ld-topup" },
            { label: "Affiliate program", href: "#ld-partner" },
          ] },
          { title: "More", links: [
            { label: "qlolmusic — label", href: "/music" },
            { label: "Support", href: "" },
            { label: "Questions and answers", href: "#ld-faq" },
          ] },
        ],
        legal: [
          "© 2026 lolq.ai",
          "rights to the clips you make stay with you",
          "card payments via Stripe, roubles via YooKassa",
        ],
        soon: "soon",
        periodAria: "billing period",
        rangeAria: "credits pack size",
      },
    },

    // ─────────── qlolmusic: лейбл, дистрибуция, мастеринг (music.html) ───────────
    music: {
      meta: {
        title: "qlolmusic — label, distribution and mastering",
        description: "qlolmusic is the label side of lolq.ai: mastering, delivery to Spotify, Apple Music, TikTok, VK and Yandex Music through Zvonko Digital, a vertical video for the release and payouts under a contract with a company.",
      },

      nav: {
        skip: "Skip to content",
        home: "Clip studio",
        offer: "What you get",
        flow: "How a release goes",
        need: "What we need",
        faq: "FAQ",
        apply: "Apply",
        cta: "Send a demo",
        menuAria: "page sections",
        sub: "label · distribution",
      },

      hero: {
        eyebrow: "Label · distribution · mastering",
        title: "Your track on the platforms — and a video for it",
        sub: "qlolmusic is the label side of lolq.ai. We master the track, deliver the release to Spotify, Apple Music, TikTok, VK and Yandex Music through the distributor Zvonko Digital, make a vertical video for it in our own studio and pay the royalties out under a contract with our company.",
        ctaApply: "Send a demo",
        ctaFlow: "How a release goes",
        trust: "the song stays yours · one contract instead of four · a statement for every period",
        status: "The direction is starting up: the distributor contract is being signed and we are taking the first artists in. Send a demo now — every one gets an answer and a slot in the release queue.",
        packTitle: "What a release pack is",
        packCap: "The frame is from a clip the studio made — the video for a release is made the same way.",
        packAlt: "A frame from a finished vertical clip made by the lolq.ai studio",
        packItems: [
          "Master — WAV 24-bit, loudness set for the platforms",
          "Cover — 3000 × 3000, checked against store rules",
          "Metadata, ISRC and UPC issued for the release",
          "A vertical 9:16 video by the lolq.ai studio",
          "Delivery to the stores and a pitch to the editors",
        ],
      },

      platforms: {
        title: "Where the release lands",
        items: [
          "Spotify", "Apple Music", "YouTube Music", "TikTok", "Instagram",
          "VK Музыка", "Яндекс Музыка", "Звук", "Deezer", "Amazon Music",
          "Shazam", "Boom",
        ],
        note: "The exact list depends on the distributor and the territory and is written into the contract before the release — a few stores ask for extra paperwork, and some are closed in some countries.",
      },

      offer: {
        eyebrow: "What we give",
        title: "Four jobs, one contract",
        lead: "Normally a release is four different contractors: a distributor, a mastering engineer, a video crew and an accountant. Here it is one contract and one person to ask.",
        items: [
          {
            title: "Distribution through Zvonko Digital",
            text: "The release goes to the stores and streaming platforms through Zvonko Digital — a distributor with direct deals. ISRC and UPC are issued for you, the release date and the pre-save are set in advance, and the release is pitched to the editors.",
          },
          {
            title: "Mastering",
            text: "You send the mix, you get a master the platforms accept: loudness and dynamics in line with the streaming standard, no clipping, and no surprises when your track plays right after somebody else's.",
          },
          {
            title: "A video on release",
            text: "A vertical 9:16 clip for the release, made by the lolq.ai studio out of your own track — the same engine the clip service runs on. It is a separate item, not a compulsory bundle.",
            img: "/img/shots/frame-2.jpg", w: 134, h: 192,
            alt: "A frame from a clip made by the studio",
          },
          {
            title: "Reporting and payouts through a company",
            text: "The contract is with a registered company, not with a person and a card number. Every period you get a statement: which platform, how many streams, how much money — and the payment comes from the company account.",
          },
        ],
      },

      flow: {
        eyebrow: "How it goes",
        title: "From a demo to money on the account",
        lead: "Six steps. Nothing happens behind your back: the contract, the master and the metadata are agreed with you before anything is delivered anywhere.",
        steps: [
          {
            n: "01", title: "The demo",
            text: "You send a link to the track and say what you need — the whole thing or one piece of it. A rough mix is fine: we are listening to the music, not to the mastering.",
            meta: "a link is enough",
          },
          {
            n: "02", title: "The answer",
            text: "Every demo gets listened to and every demo gets an answer to the contact you left, including a no. A no is not a verdict on the track: sometimes it is simply not our lane.",
            meta: "usually within a few days",
          },
          {
            n: "03", title: "The contract",
            text: "We send the offer: the split, the term, the territory, what we do and what stays with you. You read it, you ask, and only then you sign. Numbers first, signature second.",
            meta: "term · territory · split",
          },
          {
            n: "04", title: "Master and artwork",
            text: "The mix goes into mastering, or your own master goes in as it is. The cover is checked against the store rules — size, colour profile, no logos and no URLs on it, which is what half of the rejections are about.",
            meta: "WAV 24-bit · 3000 × 3000",
          },
          {
            n: "05", title: "Delivery",
            text: "Metadata, ISRC and UPC, the release date, the lyrics and the explicit flag. The pack is delivered to the platforms two to four weeks before the date: that window is what an editorial pitch and a pre-save need.",
            meta: "2–4 weeks before the date",
          },
          {
            n: "06", title: "Release and reports",
            text: "The release comes out on every platform at once. Then a statement for each period — platform, streams, money — and the payout by the contract, once the platforms have reported.",
            meta: "statement · payout",
          },
        ],
      },

      need: {
        eyebrow: "What we need from you",
        title: "The checklist before a release",
        lead: "Nothing exotic, but the stores are strict about the details: a wrong colour profile on the cover or an unclear feature is enough for a release to come back.",
        groups: [
          {
            title: "Audio",
            items: [
              "The master: WAV, 24-bit, 44.1 kHz or higher",
              "No master? Send the mix — mastering is one of the things we do",
              "An instrumental and a clean version, if you want them in the stores",
            ],
            note: "An MP3 only travels as a demo: the stores refuse a lossy master.",
          },
          {
            title: "Cover",
            items: [
              "3000 × 3000 px, JPG or PNG, RGB",
              "No store logos, no watermarks, no links and no phone numbers",
              "The text on the cover matches the title of the release exactly",
            ],
            note: "No cover yet? The studio draws one from the track.",
          },
          {
            title: "Metadata",
            items: [
              "Title, the artist and every feature spelled the way they should be printed",
              "Genre, language, explicit or not",
              "The release date, with the delivery window in mind",
              "Lyrics, if you want them shown on the platforms",
            ],
            note: "An ISRC the track already has is kept — the streams are tied to it.",
          },
          {
            title: "Rights",
            items: [
              "You are the author, or you have a written agreement with the authors",
              "The beat: an exclusive, or a licence that allows distribution — with the file",
              "Samples: cleared, or replaced",
              "Features: their consent and their share, written down",
            ],
            note: "A claim takes the release down and freezes the money on every platform at once — that is why this part is not paperwork for its own sake.",
          },
        ],
      },

      terms: {
        eyebrow: "Rights and money",
        title: "The song stays yours",
        items: [
          "<b>We do not buy the song out.</b> The contract is a licence to distribute for a term — the authorship and the rights to the work stay with you.",
          "<b>The numbers are in the offer.</b> The split, the term and the territory are on paper before you sign, not after. They depend on what you take: distribution alone is not the same deal as distribution with mastering and a video.",
          "<b>The money comes from a company.</b> A contract, an invoice and a statement — the payout is a normal payment from a legal entity, not a transfer between cards.",
          "<b>A statement for every period.</b> Platform, streams, money. The platforms report with a lag of a couple of months — that lag is theirs, and we pass on what they send, as they send it.",
          "<b>You can leave.</b> When the term ends the release moves to another distributor and keeps its ISRC, so the streams and the playlist history survive the move.",
        ],
        note: "We deliberately publish no percentages here: the split depends on the set of work, and a single number on a landing page would be a lie by simplification. You get the numbers with the offer, before any signature.",
      },

      faq: {
        eyebrow: "Questions",
        title: "About rights, royalties and deadlines",
        items: [
          { q: "Who owns the rights to the song?",
            a: "You do. qlolmusic does not buy the track out and does not take the authorship: the contract is a licence to distribute, with a term, a territory and a split written into it. When the term is over the release can move to another distributor." },
          { q: "What is the split?",
            a: "It is fixed in the offer and you see it before you sign anything. It depends on the set of work — distribution on its own, or distribution together with mastering and a video. That is exactly why there is no single number on this page: it would be true for one artist and false for the next." },
          { q: "How long does it take to reach the platforms?",
            a: "The delivery itself takes a few days, but the stores want the pack two to four weeks before the date: that window is what an editorial pitch and a pre-save need. An urgent release is possible — without the pitch." },
          { q: "Do you need exclusivity?",
            a: "For the release we distribute, yes: the same release cannot go through two distributors at once, the stores read it as a duplicate and take it down. The rest of your catalogue is none of our business." },
          { q: "My track is already out through another distributor. Can you take it over?",
            a: "Yes, a transfer is routine: you take the release down at the old distributor or wait for the term to end, and we deliver it again keeping the ISRC. Streams, playlist positions and the Shazam history are tied to the ISRC and survive the move." },
          { q: "What about samples and leased beats?",
            a: "A sample from somebody else's record has to be cleared, and a beat lease has to allow distribution — send the licence file along with the demo. If the rights are not clean, the store takes the release down and the money goes back to the platform, so this is the one place where we are boring on purpose." },
          { q: "When and how are the payouts made?",
            a: "The platforms report with a lag, usually two to three months after the month the streams happened. For every period we pass on a statement and pay out by the contract from the company account, in the currency the contract names." },
          { q: "Do I have to take the video?",
            a: "No. Distribution and mastering work on their own, and the clip is a separate item — and the other way round too: you can come only for a video and keep your current distributor." },
        ],
      },

      form: {
        eyebrow: "Apply",
        title: "Send a demo",
        lead: "One form. We listen to everything and answer to the contact you leave — including a no.",
        sideTitle: "What happens next",
        sideItems: [
          "We listen to the demo and answer to the contact you left",
          "If it fits, the offer comes with the split and the term already in it",
          "You sign only after you have read the numbers",
        ],
        nameLabel: "Name or artist name",
        namePh: "how to address you",
        contactLabel: "Contact",
        contactPh: "email or @telegram",
        contactHint: "Where the answer goes: an email or a Telegram handle.",
        demoLabel: "Link to the demo",
        demoPh: "https://… — SoundCloud, Drive, YouTube, a Telegram post",
        demoHint: "Any link that opens without a password. A rough mix is fine.",
        needLabel: "What do you need",
        needOptions: [
          { id: "distribution", label: "Distribution" },
          { id: "mastering", label: "Mastering" },
          { id: "clip", label: "A video" },
          { id: "all", label: "Everything at once" },
        ],
        commentLabel: "Anything else",
        commentPh: "Release date, how many tracks, whether there is a cover and a master, links to your other releases — anything that helps.",
        submit: "Send the application",
        sending: "sending…",
        consent: "By sending it you agree that we may write back to the contact you left. Nothing else is done with it.",
        errName: "Tell us how to address you",
        errContact: "Leave an email or a @telegram — otherwise there is nowhere to answer",
        errContactBad: "That does not look like an email or a Telegram handle",
        errDemo: "A link to the demo is the whole point of the form",
        errDemoBad: "That does not look like a link — it should start with http:// or https://",
        errNeed: "Pick what you need",
        okTitle: "The application is in",
        okText: "We listen to every demo and answer to the contact you left, usually within a few days.",
        okAgain: "Send another one",
        offTitle: "Applications are not switched on yet",
        offText: "The form is not connected to the server yet, so the application was NOT sent — we are not going to pretend otherwise. Copy the text and send it to {email}, or come back in a couple of days.",
        offTextNoMail: "The form is not connected to the server yet, so the application was NOT sent — we are not going to pretend otherwise. Copy the text with the button below and keep it, or come back in a couple of days.",
        offCopy: "Copy the application",
        offMail: "Write to us",
        copied: "copied",
        copyFail: "the browser blocked the copy — select the text by hand",
        failNet: "The server did not answer, so the application was not sent. Check the connection and try again.",
        failBusy: "Too many applications from this address. Try again in a few minutes.",
        failServer: "The server answered with an error, so the application was not sent: {msg}",
        failGeneric: "Could not send the application: {msg}",
        mailSubject: "qlolmusic — application",
      },

      footer: {
        about: "qlolmusic is the label side of lolq.ai: distribution, mastering and a video for the release, under one contract.",
        cols: [
          { title: "qlolmusic", links: [
            { label: "What you get", href: "#mu-offer" },
            { label: "How a release goes", href: "#mu-flow" },
            { label: "What we need", href: "#mu-need" },
            { label: "Send a demo", href: "#mu-apply" },
          ] },
          { title: "Studio", links: [
            { label: "lolq.ai — clips", href: "/" },
            { label: "How the studio works", href: "/#ld-how" },
            { label: "Pricing", href: "/#ld-pricing" },
            { label: "Styles breakdown", href: "/report/styles.html" },
          ] },
          { title: "More", links: [
            { label: "Questions and answers", href: "#mu-faq" },
            { label: "Support", href: "" },
          ] },
        ],
        legal: [
          "© 2026 lolq.ai",
          "the rights to the songs stay with their authors",
          "distribution through Zvonko Digital, contract with a legal entity",
        ],
        soon: "soon",
      },
    },
  },

  // ═══════════════════════════════ РУССКИЙ ═══════════════════════════════
  ru: {
    common: {
      save: "Сохранить",
      saving: "сохраняю…",
      cancel: "отмена",
      create: "Создать",
      close: "закрыть",
      del: "Удалить",
      delTitle: "удалить",
      copy: "скопировать",
      copied: "скопировано",
      copyManual: "скопируй вручную",
      loading: "загружаю…",
      generate: "Сгенерировать",
      generating: "генерирую…",
      loadFail: "не вышло загрузить",
      madeWith: "сделано :333",
      moveUp: "выше",
      moveDown: "ниже",
      prev: "назад",
      next: "вперёд",
    },

    lang: {
      aria: "Язык интерфейса",
      en: "EN",
      ru: "RU",
      switchTitle: "Переключить язык интерфейса",
    },

    meta: {
      description: "lolq.ai — студия ИИ-клипов под собственную музыку: загрузил трек — забрал вертикальный клип 9:16 со сквозными персонажами.",
    },

    status: {
      queued: "в очереди…",
      running: "генерирую…",
      error: "ошибка",
      done: "готово",
    },

    auth: {
      loginPh: "логин (пусто — вход владельца)",
      passwordPh: "пароль",
      submit: "войти",
      fail: "неверный логин или пароль",
    },

    top: {
      brandTitle: "lolq.ai — студия клипов",
      coverTitle: "обложка проекта — клик, чтобы заменить",
      projectNamePh: "название клипа",
      newProject: "+ проект",
      pointsTitle: "остаток генераций",
      pointsUnit: "очков",
      account: "Кабинет",
      accountTitle: "тариф, генерации, партнёрка",
      saveAccount: "Сохранить аккаунт",
      logout: "выйти",
      kindAlbum: "альбом",
      kindSingle: "сингл",
      planPro: "{plan} · Seedance",
      planFree: "FREE · Grok",
      planProTitle: "Seedance доступен: монтаж по первому и последнему кадру",
      planFreeTitle: "бесплатный тариф: видео через Grok, Seedance откроется на платном",
    },

    story: {
      title: "Герой и сюжет",
      bible: "Библия героя",
      bibleTitle: "внешность, характер — одна на все треки",
      biblePh: "Оставь пустым — придумается по первым трекам",
      arc: "Сквозной сюжет",
      arcPh: "Появится после генерации",
      gen: "Сгенерировать сюжет по всем трекам",
    },

    chars: {
      title: "Персонажи альбома",
      titleHint: "имя + характер + фото-модельки: распространяются на весь альбом, в промптах кадров пишутся имена, внешность подтягивается всегда",
      add: "+ добавить персонажа",
    },

    tracks: {
      title: "Треки",
      add: "+ добавить трек",
      titlePh: "Название трека",
      styleLabel: "Стиль клипа (1–3 пресета, первый — основа)",
      lyricsPh: "Текст песни (необязательно — можно чисто бит: тогда идею закладывают комментарий и персонажи)",
      commentPh: "Комментарий: что имел в виду, контекст",
      submit: "Добавить трек",
    },

    stages: {
      setup: "Настройка",
      board: "Раскадровка",
      anim: "Анимация",
    },

    stylePicker: {
      descSummary: "описание выбранных стилей",
      none: "стиль не выбран",
      base: "основа",
      plus: "плюс",
      custom: "(у трека свой кастомный стиль — выбор пресета заменит его)",
    },

    track: {
      coverTitle: "обложка трека — клик, чтобы заменить",
      titlePh: "Название",
      supergen: "⚡ Супергенерация",
      supergenBusy: "⚡ генерирую всё…",
      supergenTitle: "весь конвейер одной кнопкой",
      delTitle: "удалить",
      delConfirm: "Удалить трек вместе с раскадровкой?",
      styleLabel: "Стиль клипа",
      styleLabelTitle: "1–3 пресета чекбоксами: первый выбранный — основа микса",
      audio: "Аудио",
      audioProfile: "профиль дорожки",
      grain: "плёночное зерно на весь клип",
      grainTitle: "ffmpeg-зерно 16 мм поверх всего собранного клипа",
      nostory: "без сюжета (рандомные кадры)",
      nostoryTitle: "раскадровка = независимые рандомные панч-кадры по комментарию, сквозной сюжет не нужен",
      comment: "Комментарий",
      commentPh: "что имел в виду, контекст",
      lyrics: "Текст песни",
      lyricsPh: "Пусто = чисто бит: идею закладывают комментарий и персонажи",
      saveTrack: "Сохранить трек",
      noteLabel: "Режиссёрская заметка трека",
      genScenes: "Сгенерировать раскадровку",
      genScenesTitle: "сначала сгенерируй общий сюжет — панель «Сюжет и герой» выше",
      scenesCount: "кадров: {n}",
      scenesDone: "готово, кадров: {n}",
      sheetTitle: "Лист раскадровки",
      sheetEmpty: "листа ещё нет",
      sheetOpen: "Открыть крупно",
      sheetOpenTitle: "показать лист во весь экран",
      genSheet: "Сгенерировать лист",
      redrawSheet: "Перерисовать лист",
      sheetBusy: "рисую лист…",
      sliceSheet: "Разложить лист по кадрам",
      sliceTitle: "нарезать лист-сетку: каждая ячейка станет первым кадром своей сцены",
      addScene: "+ кадр вручную",
      allFrames: "Сгенерировать кадры всех сцен",
      allFramesN: "Кадры всех сцен ({n})",
      allFramesBusy: "генерирую кадры…",
      allFramesTitle: "очередь идёт по одной сцене — вкладку можно закрыть, прогресс не теряется",
      allFramesNote: "очередь идёт по одной сцене",
      allVideos: "Видео всех сцен",
      allVideosN: "Видео всех сцен ({n})",
      allVideosBusy: "генерирую видео…",
      allVideosTitle: "поставить в очередь видео по всем сценам с готовыми кадрами",
      allVideosConfirm: "Поставить в очередь видео для {n} сцен? Это спишет кредиты видеогенератора.",
      animEmpty: "Видео ещё нет — сгенерируй их из «Раскадровки»: кнопка «Видео сцены» на карточке кадра.",
      clipHead: "Готовый клип трека",
      clipTitle: "Готовый клип — утверждено сцен: {a}/{b}",
      clipEmpty: "клипа ещё нет",
      assemble: "Собрать клип",
      reassemble: "Пересобрать",
      assembleBusy: "собираю клип…",
      assembleTitle: "утверди хотя бы одну сцену (этап «Анимация»)",
      autoAsm: "автосборка",
      autoAsmTitle: "каждое новое видео сцены само идёт в клип, и клип пересобирается — кнопку нажимать не нужно",
      autoAsmOn: "включена: новые видео сцен идут в клип",
      clipDone: "клип готов",
      download: "скачать",
      finalScenes: "Видео всех сцен",
      finalEmpty: "видео сцен ещё нет — они появятся здесь после этапа «Анимация»",
    },

    scene: {
      pos: "Кадр {n}",
      playTitle: "прослушать этот кадр",
      editTitle: "редактировать кадр",
      delTitle: "удалить кадр",
      delConfirm: "Удалить кадр?",
      firstFrame: "первый кадр",
      lastFrame: "последний кадр",
      firstCap: "перв.",
      lastCap: "посл.",
      midThumb: "промежуточный кадр {n}",
      refsTitle: "референсы кадра: композиция, свет, вайб",
      refTitle: "референс кадра — клик: открыть оригинал",
      refDel: "убрать референс",
      refAdd: "+ реф",
      refUploadTitle: "картинка-образец: композиция, свет, вайб кадра",
      charsTitle: "персонажи в кадре — клик включает/выключает",
      prompt: "промпт",
      promptTitle: "промпт первого кадра",
      durationTitle: "секунд",
      shotSizeTitle: "крупность плана",
      shotSizeEmpty: "крупность —",
      shot: {
        "extreme close-up": "деталь",
        "close-up": "крупный",
        medium: "средний",
        wide: "общий",
        establishing: "заявочный",
      },
      cameraPh: "движение камеры",
      cameraTitle: "напр. slow push-in",
      lyricPh: "строка лирики",
      notePh: "что в кадре",
      motionPh: "промпт анимации кадра",
      motionLastPh: "промпт последнего кадра сцены",
      save: "Сохранить кадр",
      genFrames: "Сгенерировать кадры",
      regenFrames: "Перегенерировать кадры",
      framesBusy: "рисую кадры…",
      genFirst: "⟳ перв.",
      genFirstTitle: "пересобрать только первый кадр — последний останется",
      genLast: "⟳ посл.",
      genLastTitle: "пересобрать только последний кадр — первый останется",
      midBtn: "+ промежуточные кадры ({n})",
      midBusy: "промежуточные {a}/{b}…",
      midShort: "сцена короткая — промежуточные не нужны",
      midNoFrame: "сначала сгенерируй кадры сцены",
      midTitle: "по одному кадру между первым и последним, 1 очко за кадр",
      providerTitle: "провайдер видео",
      providerSeedance: "Seedance (2 кадра)",
      providerGrok: "Grok (1 кадр)",
      providerSeedanceShort: "Seedance",
      providerGrokShort: "Grok",
      genVideo: "Видео сцены",
      regenVideo: "Перегенерировать видео",
      videoBusy: "генерирую…",
      videoNoFrame: "сначала кадр",
      videoTitleNoFrame: "Сначала сгенерируй кадры этой сцены — видео делается из первого и последнего кадра",
      videoTitle: "Оживить сцену: первый + последний кадр → видео",
      approve: "в клип",
      approveTitle: "утверждённые сцены идут в общий клип",
      approveNeedVideo: "сначала сгенерируй видео сцены",
      audioTitle: "отрезок трека под сцену",
      cap: "Сцена {n} · {time}",
      capApproved: " · в клип",
    },

    character: {
      namePh: "Имя персонажа",
      noName: "без имени",
      openTitle: "клик: досье персонажа",
      attrsN: "атрибутов: {n}",
      attrsNone: "без атрибутов",
      main: "главный",
      delTitle: "удалить",
      delConfirm: "Удалить персонажа «{name}» вместе с фото?",
      descLabel: "Характер и внешность",
      descTitle: "пойдёт в промпты дословно",
      upload: "+ фото-моделька",
      genModel: "Сгенерировать модельку",
      genModelTitle: "сгенерировать разворот персонажа в 4 ракурсах по описанию и приложенным фото",
      photoDel: "удалить фото",
      attrs: "Атрибуты",
      attrsTitle: "фирменные вещи персонажа: шляпа, очки, квадрик…",
      attrAdd: "+ атрибут",
      attrEditTitle: "клик: редактировать",
      attrDelTitle: "удалить атрибут",
      attrPhotoAdd: "+ фото",
    },

    modal: {
      closeTitle: "закрыть",

      saveAccount: {
        title: "Сохранить аккаунт",
        lead: "Логин и пароль закрепят этот аккаунт: проекты и файлы останутся при тебе на любом устройстве.",
        loginLabel: "Логин",
        loginPh: "логин",
        passLabel: "Пароль (от 6 символов)",
        passPh: "пароль",
        nameLabel: "Имя (необязательно)",
        namePh: "как к тебе обращаться",
      },

      newProject: {
        title: "Новый проект",
        nameLabel: "Название",
        namePh: "Название проекта",
        kindLabel: "Тип проекта",
        album: "Альбом",
        albumNote: "несколько треков",
        single: "Сингл",
        singleNote: "один трек",
        coverLabel: "Обложка (необязательно)",
        coverHint: "＋ выбрать файл (jpg / png / webp)",
        nameRequired: "введи название",
      },

      addChar: {
        title: "Добавить персонажа",
        tabNew: "Новый",
        tabLibrary: "Из базы",
        nameLabel: "Имя персонажа",
        namePh: "напр. Артём",
        nameRequired: "введи имя",
        libLead: "Персонаж из любого проекта: имя, описание и фото-модельки скопируются в текущий.",
        libEmpty: "в базе пока никого нет",
        libHere: "уже здесь",
      },

      attribute: {
        newTitle: "Новый атрибут",
        editTitle: "Атрибут персонажа",
        nameLabel: "Название (напр. красная кепка)",
        namePh: "как зовётся вещь",
        descLabel: "Описание (необязательно — пойдёт в промпты)",
        nameRequired: "введи название",
        delTitle: "Удалить атрибут?",
        delText: "Атрибут «{name}» и все его фото будут удалены.",
      },

      supergen: {
        title: "⚡ Супергенерация",
        styleOk: "Стиль клипа выбран",
        styleBad: "Стиль клипа НЕ выбран — выбери пресет на карточке трека",
        charsOk: "Персонажи: {names}",
        charsBad: "В проекте НЕТ персонажей — добавь нового или клонируй из базы",
        ideaOk: "Идея есть (текст или комментарий)",
        ideaBad: "Нет ни текста, ни комментария — впиши идею клипа в комментарий",
        info: "Сквозной сюжет — по желанию: впиши свой в блоке «Герой и сюжет» или оставь пустым, напишу сам. Дальше всё автоматом: сюжет → раскадровка → кадры → видео каждой сцены → сборка клипа с треком. Прогресс будет виден на карточке трека. Если сцены уже были сгенерены с другим стилем — сначала нажми «Сгенерировать раскадровку» заново.",
        go: "Погнали",
      },

      sheet: {
        title: "Лист раскадровки",
        full: "Реальный размер",
        fit: "Вписать в экран",
        original: "открыть оригинал",
      },

      character: {
        title: "Персонаж",
      },

      cells: {
        title: "Разложить лист по кадрам",
        hint: "Отметь ячейки, которые берём, и выбери сцену для каждой. Остальные сцены не тронутся — их можно перегенерировать отдельно.",
        toScene: "в кадр {n}",
        apply: "Применить выбранные",
        nonePicked: "не выбрано ни одной ячейки",
        applied: "Разложено кадров: {n}",
      },

      model: {
        title: "Моделька: {name}",
        someone: "персонаж",
        withPhotos: "Референсом уйдут первые {n} фото персонажа — лицо и одежда сохранятся.",
        noPhotos: "Фото не загружены — моделька будет собрана только по описанию.",
        descLabel: "Описание для модельки",
        descPh: "внешность, одежда, атрибуты — чем подробнее, тем точнее",
        kindLabel: "Вид модельки",
        kind3d: "3D-модель (CG-рендер)",
        kindReal: "Фотореализм (студия)",
        kindAnime: "Аниме (лист сеттеи)",
        busy: "генерирую… (до 2 минут)",
      },
    },

    account: {
      title: "Кабинет",
      tabs: {
        account: "Аккаунт",
        plan: "Тариф",
        ref: "Амбассадор",
        payouts: "Выплаты",
      },
      guest: "гость",
      noContacts: "аккаунт без почты и логина",
      statPlan: "тариф",
      statUntil: "активен до",
      statPoints: "генераций",
      statProjects: "проектов",
      logins: "Входы в аккаунт",
      password: "Пароль",
      yandex: "Яндекс",
      autopayOff: "Отключить автопродление",
      autopayOffNote: "тариф доработает до конца оплаченного срока",
      autopayOffBusy: "отключаю…",
      autopayOffDone: "автопродление отключено",
      autopayNote: "автопродление выключено",
      choosePlan: "Выбрать тариф",
      renewPlan: "Продлить тариф",
    },

    plan: {
      pointsLine: "{n} генераций",
      current: "текущий",
      basic: "базовый",
      pay: "Оплатить",
      promoLabel: "Промокод",
      promoPh: "код амбассадора — скидка на первую оплату",
      creating: "создаю платёж…",
      payOff: "оплата пока не подключена",
      payOffTitle: "приём платежей ещё не настроен — напиши владельцу",
      payOffNote: "Приём платежей ещё не подключён — тарифы показаны, оплатить нельзя.",
      noUrl: "касса не вернула ссылку оплаты",
    },

    ref: {
      joinLead: "Приводи людей по своей ссылке — и забирай долю с их оплат.",
      joinDiscount: "другу — скидка <b>{pct}%</b> на первую оплату",
      joinReward: "тебе — <b>{pct}%</b> с каждого его платежа, не только с первого",
      joinPayout: "выплата по твоим реквизитам от {sum}",
      join: "Стать амбассадором",
      joining: "подключаю…",
      codeLabel: "Твой промокод",
      linkLabel: "Реферальная ссылка",
      note: "Другу — скидка {discount}% на первую оплату, тебе — {reward}% с каждой его оплаты.",
      statInvited: "приглашено",
      statBuyers: "оплатили",
      statAccrued: "начислено",
      statPaid: "выплачено",
      statAvailable: "доступно",
      turnover: "оборот приглашённых — {sum}",
      reserved: ", в заявках — {sum}",
      eventsLabel: "Последние события",
      eventPayment: "оплата {sum}",
      eventVisit: "пришёл по ссылке",
      eventsEmpty: "пока пусто — поделись ссылкой",
      detailsLabel: "Реквизиты для выплаты",
      detailsPh: "карта, СБП по номеру телефона, кому переводить",
      detailsSave: "Сохранить реквизиты",
      detailsSaved: "реквизиты сохранены",
      payoutLabel: "Заказать выплату",
      payoutPh: "сумма в ₽",
      payoutBtn: "Заказать выплату",
      payoutNote: "Пусто в сумме — закажем всё доступное. Минимальная выплата — {sum}, деньги по заявке сразу уходят в резерв.",
      payoutBusy: "отправляю заявку…",
      payoutDone: "заявка принята — деньги уйдут по реквизитам",
      myPayouts: "Мои заявки",
    },

    payouts: {
      queue: "Очередь",
      filterNew: "новые",
      filterPaid: "выплаченные",
      filterRejected: "отклонённые",
      filterAll: "все",
      empty: "заявок в этой очереди нет",
      noDetails: "реквизиты не указаны",
      markPaid: "Выплачено",
      markRejected: "Отклонить",
      statusNew: "в работе",
      statusPaid: "выплачено",
      statusRejected: "отклонена",
    },

    styles: {
      ghibli: {
        label: "Хаяо Миядзаки (ламповое аниме)",
        desc: "Тёплое рисованное аниме с акварельными фонами и уютным светом — как кадр из Гибли.",
      },
      pixar: {
        label: "3D мультяшный (Pixar-style)",
        desc: "Глянцевый 3D-мультфильм: выразительные герои, сочный кинематографичный свет.",
      },
      shinkai: {
        label: "Кинематографичное аниме (Синкай)",
        desc: "Современное аниме с гиперкрасивыми небесами, бликами и эмоциональными градиентами.",
      },
      cinema: {
        label: "Реализм (кино)",
        desc: "Фотореалистичное кино на плёнке: честный свет, фактура кожи, лёгкое зерно.",
      },
      flat2d: {
        label: "2D плоская анимация",
        desc: "Яркая плоская векторная анимация: простые формы, смелые контуры, постерные композиции.",
      },
      noir: {
        label: "Нуарный комикс",
        desc: "Чёрно-белый нуар с одним цветовым акцентом: глубокие тени, дождь, неон.",
      },
      longheads: {
        label: "Длинные бошки (аналоговый сюр 90-х)",
        desc: "Аналоговая плёнка 90-х: сюрреалистичные длинноголовые персонажи в обычной уличной жизни.",
      },
      embroidery: {
        label: "Картон (вышивка нитью)",
        desc: "Весь кадр вышит нитью по кремовому фетру и крафту — тёплая ручная работа.",
      },
      spike: {
        label: "СПАЙК (русский кино-сюр, камео)",
        desc: "Ночной русский кино-сюр: хрущёвки, Лады, дым и мультяшные камео на серьёзных щах.",
      },
      munir: {
        label: "МУНИР (залив, вспышка, фиш-ай)",
        desc: "Уличная съёмка Залива со вспышкой и фиш-аем: кольца в объектив, G63, доберманы.",
      },
      fanuel: {
        label: "ФАНУЕЛ (кино-сюрреализм, огонь)",
        desc: "Сюрреалистичный fashion-фильм: одинокая фигура в костюме среди невозможных пейзажей и огня.",
      },
      clay: {
        label: "Клеймация (пластилин)",
        desc: "Пластилиновая стоп-моушен анимация: отпечатки пальцев, миниатюрные декорации, тёплый свет.",
      },
      punkrf: {
        label: "ПАНКРФ (найденное видео, дичь РФ)",
        desc: "Гиперреалистичное «случайное видео» ночной России: регистраторы и VHS, красный неон, абсурд среди пробок и панелек.",
      },
      dreamclad: {
        label: "ДРИМКЛАД (hood-кино 90-х)",
        desc: "Плёночное hood-кино 90-х: зерно, белые майки и банданы, деньги, голуби и кресты, иконописные фронтальные композиции.",
      },
      katsumi: {
        label: "КАТСУМИ (найденная плёнка, сюр)",
        desc: "Гиперреалистичная «найденная плёнка»: крысы, монахи и алиены на полном серьёзе живут бытовухой под камкордер со вспышкой из 90-х.",
      },
    },

    guide: {
      title: "Как это работает",
      chars: {
        title: "Персонажи с модельками",
        text: "Заводишь героя: грузишь его фото или жмёшь «Сгенерировать модельку». Лицо и одежда держатся во всех кадрах, а квадрик, шлем и очки живут отдельными атрибутами.",
        alt: "Карточка персонажа: описание внешности, фото-модельки и атрибуты",
      },
      setup: {
        title: "Трек и стиль",
        text: "Грузишь аудио и выбираешь 1–3 пресета стиля — они смешиваются в один вайб. Текст песни не обязателен: идею закладывает комментарий, а режим «без сюжета» соберёт клип из рандомных панч-кадров.",
        alt: "Этап «Настройка»: чипы стилей, аудио трека, комментарий и галочки",
      },
      board: {
        title: "Раскадровка",
        text: "Claude режет трек на сцены под реальную динамику дорожки. Лист-эскиз раскладывается по кадрам выборочно, любой кадр перерисовывается отдельно — только первый или только последний, — и к нему можно приложить свои референсы.",
        alt: "Этап «Раскадровка»: лист-эскиз и лента карточек кадров с миниатюрами",
      },
      anim: {
        title: "Анимация",
        text: "Каждая сцена оживает по первому и последнему кадру (Seedance) или по одному первому (Grok). Одной кнопкой ставится очередь на все сцены сразу.",
        alt: "Этап «Анимация»: видео сцен с отметкой «в клип»",
      },
      ready: {
        title: "Готовый клип",
        text: "Утверждённые сцены склеиваются с твоей дорожкой. Есть плёночное зерно на весь клип и скачивание готового файла.",
        alt: "Этап «Готовое»: собранный клип и сетка видео всех сцен",
      },
    },

    errors: {
      generic: "что-то пошло не так",
      network: "нет связи с сервером",
      unauthorized: "сессия истекла — войди заново",
      codes: {
        network: "Нет связи с сервером. Проверь сеть и попробуй ещё раз.",
        unauthorized: "Сессия истекла — войди заново.",
        not_enough_points: "Не хватает очков: шаг стоит {need}, у тебя {have}. Докупи {short} или подними тариф.",
        payments_disabled: "Приём платежей ещё не подключён.",
        unknown_plan: "Неизвестный тариф.",
        unknown_pack: "Неизвестный пакет очков.",
        stripe_failed: "Stripe не принял платёж. Попробуй через минуту.",
        yookassa_failed: "ЮKassa не приняла платёж. Попробуй через минуту.",
      },
    },

    landing: {
      nav: {
        skip: "К содержанию",
        how: "Как это работает",
        features: "Возможности",
        pricing: "Тарифы",
        faq: "Вопросы",
        music: "qlolmusic",
        signin: "Войти",
        start: "Начать бесплатно",
        open: "Открыть студию",
        menuAria: "разделы страницы",
      },

      hero: {
        eyebrow: "Клипы под собственную музыку",
        title: "Загрузил трек — забрал клип",
        sub: "Студия режет песню на сцены под её же динамику, рисует кадры в выбранном стиле и оживляет их. Вертикаль 9:16, сквозные персонажи, покадровый контроль. Первый клип — бесплатно и без регистрации.",
        ctaStart: "Начать бесплатно",
        ctaOpen: "Открыть студию",
        ctaLogin: "У меня есть аккаунт",
        trust: "без регистрации · 120 очков на первый клип · файлы приватны",
        trustBack: "аккаунт на месте · проекты и файлы ждут в студии",
        guideLink: "Посмотреть, как это выглядит внутри →",
        phoneCap: "0:26 · 9:16",
        proofMeta: "трек «Bully» · 6 сцен · 26 секунд",
        proofCap: "Клип и кадры собраны студией целиком — по одному загруженному биту, руками ничего не дорисовано.",
        proofAltClip: "Кадр готового клипа: герой в белом шлеме на квадроцикле у ночного ларька",
        refBanner: "Ты пришёл по приглашению {code} — скидка {discount}% на первую оплату",
      },

      how: {
        eyebrow: "Как это работает",
        title: "Четыре шага от трека до готового клипа",
        lead: "Ни одного экрана «подожди, происходит магия»: после каждого шага видно результат, и любой кусок переделывается отдельно.",
        steps: [
          {
            n: "01",
            title: "Трек и стиль",
            text: "Грузишь mp3 и отмечаешь до трёх стилей из пятнадцати — они смешиваются в один вайб, первый выбранный становится основой. Текста песни может не быть: контекст объясняешь комментарием.",
            meta: "15 стилей · микс до трёх · чистый бит тоже годится",
            img: "/img/shots/step-track.jpg", w: 875, h: 300,
            alt: "Экран настройки трека: чипы стилей, аудио, комментарий",
          },
          {
            n: "02",
            title: "Сюжет и раскадровка",
            text: "Модель слушает длительность и структуру дорожки и режет её на сцены с таймкодами, крупностями и движением камеры. Рядом собирается лист-эскиз всей раскадровки — сразу видно, ровно ли идёт история.",
            meta: "сцены с таймкодами · лист-эскиз · сквозной сюжет или рандомные панч-кадры",
            img: "/img/shots/step-board.jpg", w: 700, h: 478,
            alt: "Лист раскадровки: сетка из шести кадров будущего клипа",
          },
          {
            n: "03",
            title: "Кадры сцен",
            text: "У каждой сцены свой первый и последний кадр — из них потом собирается движение. Не понравился кадр — перерисовывается один он, а не вся сцена; можно приложить свой референс композиции или света.",
            meta: "перв./посл. кадр · перегенерация по одному · свои референсы",
            img: "/img/shots/step-frames.jpg", w: 875, h: 330,
            alt: "Карточки сцен с первым и последним кадром и кнопками перегенерации",
          },
          {
            n: "04",
            title: "Клип",
            text: "Утверждённые сцены склеиваются с твоей дорожкой в один вертикальный клип. Плёночное зерно на весь ролик — галочкой, готовый файл скачивается.",
            meta: "9:16 · плёночное зерно · mp4 на выходе",
            img: "/img/shots/step-clip.jpg", w: 875, h: 790,
            alt: "Экран готового клипа: собранный ролик и видео всех сцен",
          },
        ],
      },

      features: {
        eyebrow: "Что внутри",
        title: "Инструменты, а не одна кнопка «сделать красиво»",
        lead: "Всё, чем клип отличается от набора случайных картинок: постоянные герои, единый стиль и возможность влезть в любой кадр.",
        items: [
          {
            title: "Персонажи с моделькой и атрибутами",
            text: "Заводишь героя один раз: описание, фото или сгенерированная моделька в четырёх ракурсах. Лицо и одежда держатся во всех сценах альбома, а шлем, очки и квадрик живут отдельными атрибутами и подставляются по имени.",
            img: "/img/shots/feat-chars.jpg", w: 875, h: 240,
            alt: "Карточка персонажа: описание, фото-модельки и атрибуты",
            wide: true,
          },
          { title: "15 стилей и миксы",
            text: "От ламповой рисовки и Синкая до найденной плёнки и пластилина. Можно смешать до трёх — получится узнаваемый свой." },
          { title: "Раскадровка под динамику трека",
            text: "Сцены нарезаются по длительности и структуре дорожки, а не по ровной сетке: у бита свои таймкоды." },
          { title: "Покадровая перегенерация",
            text: "Не устроил один кадр — пересобирается только он: первый, последний или промежуточные. Остальная сцена остаётся как есть." },
          { title: "Плёночное зерно 16 мм",
            text: "Зерно накладывается на весь собранный клип одним проходом — не по кадрам, поэтому картинка не мерцает." },
          { title: "Вертикаль 9:16 сразу",
            text: "Кадры, движение и сборка изначально вертикальные: клип уходит в Reels и Shorts без пересборки и полос по краям." },
          { title: "Публикация в Instagram",
            text: "Готовый клип уезжает в Reels прямо из студии — без выгрузки файла на телефон.",
            tag: "в работе" },
        ],
      },

      pricing: {
        eyebrow: "Тарифы",
        title: "Платишь за движки, а не за интерфейс",
        lead: "Очки — единая валюта работы: сцена на Grok стоит 4, на Seedance 2.0 — 10, на Seedance 2.5 и Kling — 16. Трёхминутный клип — это примерно 30 сцен.",
        month: "Помесячно",
        year: "На год",
        yearSave: "−20%",
        free: "$0",
        forever: "навсегда",
        perMonth: "/ мес",
        yearHint: "или {mo} в месяц при оплате за год",
        yearNote: "{total} в год · счёт раз в год",
        pointsLine: "{points} очков в месяц",
        clipsLine: "≈ {clips} {word} по 3 минуты",
        clipsLineOne: "≈ 1 клип на 3 минуты",
        clipWord: ["клип", "клипа", "клипов"],
        cta: "Выбрать {plan}",
        ctaFree: "Начать бесплатно",
        current: "твой тариф",
        creating: "создаю платёж…",
        payOff: "оплата подключается",
        payOffNote: "Приём платежей ещё не подключён: тарифы показаны честно, но кнопка оплаты пока не работает. Студия и бесплатный тариф доступны прямо сейчас.",
        noUrl: "касса не вернула ссылку оплаты",
        plans: {
          free: {
            title: "FREE",
            note: "Один полный клип на три минуты — за наш счёт.",
            engine: "grok",
            features: [
              "120 очков — хватает на клип целиком",
              "Grok: оживляет первый кадр каждой сцены",
              "Сюжет, раскадровка, персонажи и сборка",
            ],
          },
          pro: {
            title: "PRO",
            note: "Seedance 2.0: движение между первым и последним кадром.",
            engine: "seedance",
            features: [
              "700 очков каждый месяц",
              "Seedance 2.0 — честный монтаж по двум кадрам",
              "Неистраченные очки переносятся, до двух норм",
            ],
          },
          pro_max: {
            title: "PRO MAX",
            note: "Seedance 2.5 и Kling — те самые, которые красиво.",
            engine: "top",
            badge: "Самый ходовой",
            hi: true,
            features: [
              "2400 очков каждый месяц",
              "Seedance 2.5 и Kling открыты",
              "Неистраченные очки переносятся, до двух норм",
            ],
          },
          studio: {
            title: "STUDIO",
            note: "Объём альбома на любом движке.",
            engine: "top",
            badge: "Для лейблов",
            features: [
              "6000 очков каждый месяц — около дюжины клипов",
              "Все движки, включая Seedance 2.5 и Kling",
              "Приоритетная обработка и прямая поддержка",
            ],
          },
        },
      },

      topup: {
        eyebrow: "Докупка очков",
        title: "Кончились очки посреди альбома — добери, не меняя тариф",
        lead: "Пакет докупается поверх подписки. Чем больше пакет, тем дешевле очко.",
        note: "Докупленные очки не сгорают при продлении тарифа и не упираются в потолок накопления — ты заплатил за них отдельно.",
        priceUnit: "за пакет",
        decimalSep: ",",
        pointsUnit: "{points} очков",
        save: "−{pct}% к цене очка",
        clipsTop: "≈ {clips} {word} по 3 мин на Seedance 2.5",
        clipsGrok: "или ≈ {clips} {word} на Grok",
        cta: "Докупить {points} очков",
        creating: "создаю платёж…",
      },

      partner: {
        eyebrow: "Партнёрская программа",
        title: "Приводи своих — забирай долю",
        lead: "Амбассадор получает промокод и ссылку. Всё считается автоматически, выплаты — по реквизитам из кабинета.",
        items: [
          "другу — скидка <b>{discount}%</b> на первую оплату",
          "тебе — <b>{reward}%</b> с каждого его платежа, не только с первого",
          "статистика переходов и оплат в кабинете, заявка на выплату в один клик",
        ],
        cta: "Стать амбассадором",
        note: "Подключение занимает один клик: аккаунт создастся сам, если его ещё нет.",
      },

      faq: {
        eyebrow: "Вопросы",
        title: "Что обычно спрашивают",
        items: [
          { q: "Чей клип по правам?",
            a: "Твой. Мы не претендуем на созданное видео и не используем твои проекты как витрину без спроса. Единственное ограничение приходит от движков, которые рисуют кадры: они запрещают дипфейки реальных людей без их согласия и явно запрещённый контент." },
          { q: "Что с музыкой?",
            a: "Музыку ты приносишь свою: студия её не сочиняет и никуда не публикует. Файл лежит в приватном хранилище проекта и используется только чтобы нарезать сцены по таймкодам и склеить финальный клип." },
          { q: "Сколько идёт генерация?",
            a: "Кадры сцены — около минуты, видео сцены — от двух до пяти минут в зависимости от движка. Клип из шести сцен целиком собирается за 20–40 минут. Очередь общая и однопоточная, поэтому в час пик задача может подождать." },
          { q: "Можно ли использовать клипы коммерчески?",
            a: "Да, на любом тарифе, включая бесплатный: выкладывай в Reels, Shorts, на площадки и в рекламу. Отдельной «коммерческой лицензии» покупать не нужно." },
          { q: "Что будет, если не хватило очков?",
            a: "Генерация просто не начнётся, и сервис честно скажет, сколько очков нужно и сколько есть. Сделанное не пропадает: докупаешь пакет очков или ждёшь продления тарифа и продолжаешь с того же места." },
          { q: "Сгорают ли неистраченные очки?",
            a: "Месячная норма тарифа переносится на следующий месяц, но не больше чем на две нормы. Докупленные пакетом очки не сгорают вообще." },
          { q: "Как отменить подписку?",
            a: "Кабинет → «Аккаунт» → «Отключить автопродление». Списаний больше не будет, а оплаченный срок тариф доработает до конца — очки за него остаются твоими." },
          { q: "Нужна ли регистрация?",
            a: "Нет. «Начать бесплатно» сразу создаёт аккаунт без формы: можно работать и уйти. Логин и пароль добавляются позже одной кнопкой в студии — проекты и файлы при этом остаются те же." },
        ],
      },

      footer: {
        about: "Студия ИИ-клипов под собственную музыку. Работает в браузере: ставить ничего не нужно.",
        cols: [
          { title: "Продукт", links: [
            { label: "Как это работает", href: "#ld-how" },
            { label: "Возможности", href: "#ld-features" },
            { label: "Гайд по студии", action: "guide" },
            { label: "Разбор стилей", href: "/report/styles.html" },
          ] },
          { title: "Оплата", links: [
            { label: "Тарифы", href: "#ld-pricing" },
            { label: "Докупка очков", href: "#ld-topup" },
            { label: "Партнёрская программа", href: "#ld-partner" },
          ] },
          { title: "Ещё", links: [
            { label: "qlolmusic — лейбл", href: "/music" },
            { label: "Поддержка", href: "" },
            { label: "Вопросы и ответы", href: "#ld-faq" },
          ] },
        ],
        legal: [
          "© 2026 lolq.ai",
          "права на созданные клипы остаются у авторов",
          "оплата картой через Stripe, в рублях — через ЮKassa",
        ],
        soon: "скоро",
        periodAria: "период оплаты",
        rangeAria: "размер пакета очков",
      },
    },

    // ─────────── qlolmusic: лейбл, дистрибуция, мастеринг (music.html) ───────────
    music: {
      meta: {
        title: "qlolmusic — лейбл, дистрибуция и мастеринг",
        description: "qlolmusic — музыкальное направление lolq.ai: мастеринг, отгрузка релиза в Spotify, Apple Music, TikTok, VK и Яндекс Музыку через Zvonko Digital, вертикальный клип к релизу и выплаты по договору с юрлицом.",
      },

      nav: {
        skip: "К содержанию",
        home: "Студия клипов",
        offer: "Что даём",
        flow: "Как идёт релиз",
        need: "Что нужно от вас",
        faq: "Вопросы",
        apply: "Заявка",
        cta: "Отправить демо",
        menuAria: "разделы страницы",
        sub: "лейбл · дистрибуция",
      },

      hero: {
        eyebrow: "Лейбл · дистрибуция · мастеринг",
        title: "Ваш трек на площадках — и клип к нему",
        sub: "qlolmusic — музыкальное направление lolq.ai. Делаем мастеринг, отгружаем релиз в Spotify, Apple Music, TikTok, VK и Яндекс Музыку через дистрибьютора Zvonko Digital, снимаем к нему вертикальный клип в своей студии и платим роялти по договору с нашим юрлицом.",
        ctaApply: "Отправить демо",
        ctaFlow: "Как идёт релиз",
        trust: "песня остаётся вашей · один договор вместо четырёх · отчёт за каждый период",
        status: "Направление запускается: договор с дистрибьютором на подписании, берём первых артистов. Присылайте демо сейчас — отвечаем на каждое и держим место в очереди релизов.",
        packTitle: "Из чего состоит релиз",
        packCap: "Кадр — из клипа, собранного студией: клип к релизу делается так же.",
        packAlt: "Кадр из готового вертикального клипа, собранного студией lolq.ai",
        packItems: [
          "Мастер — WAV 24 бита, громкость под площадки",
          "Обложка — 3000 × 3000, проверена по правилам магазинов",
          "Метаданные, ISRC и UPC выпускаются на релиз",
          "Вертикальный клип 9:16 от студии lolq.ai",
          "Отгрузка в магазины и питч редакциям",
        ],
      },

      platforms: {
        title: "Куда уходит релиз",
        items: [
          "Spotify", "Apple Music", "YouTube Music", "TikTok", "Instagram",
          "VK Музыка", "Яндекс Музыка", "Звук", "Deezer", "Amazon Music",
          "Shazam", "Boom",
        ],
        note: "Точный список зависит от дистрибьютора и территории и фиксируется в договоре до релиза — часть магазинов просит отдельные документы, часть закрыта в отдельных странах.",
      },

      offer: {
        eyebrow: "Что даём",
        title: "Четыре работы и один договор",
        lead: "Обычно релиз — это четыре разных подрядчика: дистрибьютор, мастеринг-инженер, съёмочная группа и бухгалтер. Здесь один договор и один человек, которому можно задать вопрос.",
        items: [
          {
            title: "Дистрибуция через Zvonko Digital",
            text: "Релиз уходит в магазины и на стриминги через Zvonko Digital — дистрибьютора с прямыми договорами. ISRC и UPC выпускаются на вас, дата релиза и пресейв ставятся заранее, релиз питчится редакциям площадок.",
          },
          {
            title: "Мастеринг",
            text: "Присылаете сведение — получаете мастер, который принимают площадки: громкость и динамика по стриминговому стандарту, без клиппинга и без сюрпризов, когда трек играет сразу после чужого.",
          },
          {
            title: "Клип к релизу",
            text: "Вертикальный клип 9:16 под ваш трек, собранный студией lolq.ai — тем же движком, на котором работает сервис клипов. Это отдельная позиция, а не обязательный пакет.",
            img: "/img/shots/frame-2.jpg", w: 134, h: 192,
            alt: "Кадр из клипа, собранного студией",
          },
          {
            title: "Отчётность и выплаты через юрлицо",
            text: "Договор — с зарегистрированной компанией, а не с человеком и номером карты. За каждый период приходит отчёт: какая площадка, сколько прослушиваний, сколько денег, — и оплата идёт со счёта компании.",
          },
        ],
      },

      flow: {
        eyebrow: "Как это идёт",
        title: "От демо до денег на счёте",
        lead: "Шесть шагов. Ничего не делается за спиной: договор, мастер и метаданные согласуются с вами до того, как что-то куда-то уходит.",
        steps: [
          {
            n: "01", title: "Демо",
            text: "Присылаете ссылку на трек и говорите, что нужно — всё сразу или одна позиция. Черновое сведение подойдёт: слушаем музыку, а не мастеринг.",
            meta: "хватит одной ссылки",
          },
          {
            n: "02", title: "Ответ",
            text: "Слушаем каждое демо и отвечаем на оставленный контакт, в том числе отказом. Отказ — не приговор треку: иногда это просто не наша полоса.",
            meta: "обычно за несколько дней",
          },
          {
            n: "03", title: "Договор",
            text: "Присылаем оферту: доля, срок, территория, что делаем мы и что остаётся у вас. Читаете, спрашиваете и только потом подписываете. Сначала цифры, потом подпись.",
            meta: "срок · территория · доля",
          },
          {
            n: "04", title: "Мастер и обложка",
            text: "Сведение уходит в мастеринг — или ваш мастер идёт как есть. Обложку проверяем по правилам магазинов: размер, цветовой профиль, отсутствие логотипов и ссылок, на которых сыпется половина релизов.",
            meta: "WAV 24 бита · 3000 × 3000",
          },
          {
            n: "05", title: "Отгрузка",
            text: "Метаданные, ISRC и UPC, дата релиза, текст песни и метка explicit. Пакет уходит на площадки за две–четыре недели до даты: это окно нужно питчу и пресейву.",
            meta: "за 2–4 недели до даты",
          },
          {
            n: "06", title: "Релиз и отчёты",
            text: "Релиз выходит на всех площадках одновременно. Дальше — отчёт за каждый период: площадка, прослушивания, деньги, — и выплата по договору, когда площадки отчитались.",
            meta: "отчёт · выплата",
          },
        ],
      },

      need: {
        eyebrow: "Что нужно от вас",
        title: "Чек-лист перед релизом",
        lead: "Ничего экзотического, но магазины строги к мелочам: неверный цветовой профиль обложки или непрояснённый фит — уже повод вернуть релиз.",
        groups: [
          {
            title: "Аудио",
            items: [
              "Мастер: WAV, 24 бита, 44,1 кГц или выше",
              "Мастера нет? Присылайте сведение — мастеринг делаем мы",
              "Инструментал и чистая версия, если хотите их в магазинах",
            ],
            note: "MP3 едет только как демо: мастер со сжатием магазины не принимают.",
          },
          {
            title: "Обложка",
            items: [
              "3000 × 3000 px, JPG или PNG, RGB",
              "Без логотипов площадок, водяных знаков, ссылок и телефонов",
              "Текст на обложке совпадает с названием релиза буква в букву",
            ],
            note: "Обложки нет? Студия нарисует её по треку.",
          },
          {
            title: "Метаданные",
            items: [
              "Название, артист и все фиты — ровно так, как должно быть напечатано",
              "Жанр, язык, explicit или нет",
              "Дата релиза с учётом окна на отгрузку",
              "Текст песни, если хотите видеть его на площадках",
            ],
            note: "Уже выпущенный ISRC сохраняем — к нему привязаны прослушивания.",
          },
          {
            title: "Права",
            items: [
              "Вы автор — или у вас письменная договорённость с авторами",
              "Бит: эксклюзив или лицензия, разрешающая дистрибуцию, — с файлом",
              "Семплы: очищены или заменены",
              "Фиты: согласие артиста и его доля, зафиксированные на бумаге",
            ],
            note: "Претензия снимает релиз и замораживает деньги сразу на всех площадках — поэтому здесь мы занудствуем не из любви к бумагам.",
          },
        ],
      },

      terms: {
        eyebrow: "Права и деньги",
        title: "Песня остаётся вашей",
        items: [
          "<b>Мы не выкупаем песню.</b> Договор — это лицензия на распространение на срок: авторство и права на произведение остаются у вас.",
          "<b>Цифры — в оферте.</b> Доля, срок и территория на бумаге до подписи, а не после. Они зависят от набора работ: дистрибуция сама по себе — не то же самое, что дистрибуция с мастерингом и клипом.",
          "<b>Деньги идут от юрлица.</b> Договор, счёт и отчёт — выплата это нормальный платёж компании, а не перевод с карты на карту.",
          "<b>Отчёт за каждый период.</b> Площадка, прослушивания, деньги. Площадки отчитываются с задержкой в пару месяцев — задержка их, а мы передаём то, что они прислали, как они прислали.",
          "<b>Можно уйти.</b> По окончании срока релиз переезжает к другому дистрибьютору вместе с ISRC — прослушивания и история плейлистов переезд переживают.",
        ],
        note: "Проценты на этой странице мы намеренно не публикуем: доля зависит от набора работ, и одно число на витрине было бы враньём через упрощение. Цифры приходят вместе с офертой — до любой подписи.",
      },

      faq: {
        eyebrow: "Вопросы",
        title: "О правах, роялти и сроках",
        items: [
          { q: "Кому принадлежат права на песню?",
            a: "Вам. qlolmusic не выкупает трек и не забирает авторство: договор — это лицензия на распространение, в которой прописаны срок, территория и доля. Когда срок кончается, релиз можно увести к другому дистрибьютору." },
          { q: "Какая доля у артиста?",
            a: "Она фиксируется в оферте, и вы видите её до того, как что-то подписали. Доля зависит от набора работ: дистрибуция сама по себе или дистрибуция вместе с мастерингом и клипом. Ровно поэтому на странице нет одного числа — для одного артиста оно было бы правдой, для следующего враньём." },
          { q: "Сколько идёт релиз до площадок?",
            a: "Сама отгрузка — несколько дней, но магазины хотят пакет за две–четыре недели до даты: это окно нужно питчу редакциям и пресейву. Срочный релиз возможен — без питча." },
          { q: "Нужна ли эксклюзивность?",
            a: "На тот релиз, который распространяем мы, — да: один и тот же релиз не может идти через двух дистрибьюторов, магазины читают это как дубль и снимают. Остальной каталог — не наше дело." },
          { q: "Трек уже вышел через другого дистрибьютора. Можно перенести?",
            a: "Да, переезд — обычное дело: вы снимаете релиз у старого дистрибьютора или дожидаетесь конца срока, а мы отгружаем его заново с сохранением ISRC. Прослушивания, позиции в плейлистах и история Shazam привязаны к ISRC и переезд переживают." },
          { q: "А что с семплами и арендованными битами?",
            a: "Семпл из чужой записи должен быть очищен, а лицензия на бит — разрешать дистрибуцию: присылайте файл лицензии вместе с демо. Если права не чистые, магазин снимает релиз, а деньги возвращаются площадке, — это единственное место, где мы занудствуем намеренно." },
          { q: "Когда и как приходят выплаты?",
            a: "Площадки отчитываются с задержкой, обычно через два–три месяца после месяца прослушиваний. За каждый период передаём отчёт и платим по договору со счёта компании, в валюте, названной в договоре." },
          { q: "Обязательно ли брать клип?",
            a: "Нет. Дистрибуция и мастеринг работают сами по себе, клип — отдельная позиция. Обратное тоже верно: можно прийти только за клипом и остаться со своим дистрибьютором." },
        ],
      },

      form: {
        eyebrow: "Заявка",
        title: "Отправьте демо",
        lead: "Одна форма. Слушаем всё и отвечаем на оставленный контакт — в том числе отказом.",
        sideTitle: "Что будет дальше",
        sideItems: [
          "Слушаем демо и отвечаем на оставленный контакт",
          "Если подходит — присылаем оферту, в ней уже есть доля и срок",
          "Подписываете только после того, как прочитали цифры",
        ],
        nameLabel: "Имя или псевдоним",
        namePh: "как к вам обращаться",
        contactLabel: "Контакт",
        contactPh: "почта или @telegram",
        contactHint: "Куда придёт ответ: почта или ник в телеграме.",
        demoLabel: "Ссылка на демо",
        demoPh: "https://… — SoundCloud, Диск, YouTube, пост в телеграме",
        demoHint: "Любая ссылка, которая открывается без пароля. Черновое сведение подойдёт.",
        needLabel: "Что нужно",
        needOptions: [
          { id: "distribution", label: "Дистрибуция" },
          { id: "mastering", label: "Мастеринг" },
          { id: "clip", label: "Клип" },
          { id: "all", label: "Всё сразу" },
        ],
        commentLabel: "Что ещё важно",
        commentPh: "Дата релиза, сколько треков, есть ли обложка и мастер, ссылки на прошлые релизы — всё, что поможет.",
        submit: "Отправить заявку",
        sending: "отправляю…",
        consent: "Отправляя, вы соглашаетесь, что мы напишем на оставленный контакт. Больше с ним ничего не делается.",
        errName: "Напишите, как к вам обращаться",
        errContact: "Оставьте почту или @telegram — иначе некуда отвечать",
        errContactBad: "Это не похоже на почту или ник в телеграме",
        errDemo: "Ссылка на демо — смысл всей формы",
        errDemoBad: "Это не похоже на ссылку — она начинается с http:// или https://",
        errNeed: "Выберите, что нужно",
        okTitle: "Заявка у нас",
        okText: "Слушаем каждое демо и отвечаем на оставленный контакт, обычно за несколько дней.",
        okAgain: "Отправить ещё одну",
        offTitle: "Приём заявок ещё включается",
        offText: "Форма пока не подключена к серверу, поэтому заявка НЕ ушла — притворяться не будем. Скопируйте текст и пришлите его на {email} или вернитесь через пару дней.",
        offTextNoMail: "Форма пока не подключена к серверу, поэтому заявка НЕ ушла — притворяться не будем. Скопируйте текст кнопкой ниже и сохраните его или вернитесь через пару дней.",
        offCopy: "Скопировать заявку",
        offMail: "Написать нам",
        copied: "скопировано",
        copyFail: "браузер не дал скопировать — выделите текст руками",
        failNet: "Сервер не ответил, заявка не ушла. Проверьте связь и попробуйте ещё раз.",
        failBusy: "Слишком много заявок с этого адреса. Попробуйте через несколько минут.",
        failServer: "Сервер ответил ошибкой, заявка не ушла: {msg}",
        failGeneric: "Не удалось отправить заявку: {msg}",
        mailSubject: "qlolmusic — заявка",
      },

      footer: {
        about: "qlolmusic — музыкальное направление lolq.ai: дистрибуция, мастеринг и клип к релизу по одному договору.",
        cols: [
          { title: "qlolmusic", links: [
            { label: "Что даём", href: "#mu-offer" },
            { label: "Как идёт релиз", href: "#mu-flow" },
            { label: "Что нужно от вас", href: "#mu-need" },
            { label: "Отправить демо", href: "#mu-apply" },
          ] },
          { title: "Студия", links: [
            { label: "lolq.ai — клипы", href: "/" },
            { label: "Как работает студия", href: "/#ld-how" },
            { label: "Тарифы", href: "/#ld-pricing" },
            { label: "Разбор стилей", href: "/report/styles.html" },
          ] },
          { title: "Ещё", links: [
            { label: "Вопросы и ответы", href: "#mu-faq" },
            { label: "Поддержка", href: "" },
          ] },
        ],
        legal: [
          "© 2026 lolq.ai",
          "права на песни остаются у авторов",
          "дистрибуция через Zvonko Digital, договор с юрлицом",
        ],
        soon: "скоро",
      },
    },
  },
};

// ────────── русские тексты ошибок бэкенда → английский ──────────
// Бэкенд отвечает по-русски (HTTPException(400, "…")). Структурные коды
// переводятся через errors.codes, а частые текстовые — этой картой.
// Чего в карте нет — показываем как пришло: пустой экран хуже кривого перевода.
const ERR_RU_TO_EN = {
  "не авторизован": "not signed in",
  "файл не найден": "file not found",
  "файл отсутствует на диске": "the file is missing on disk",
  "проект не найден": "project not found",
  "трек не найден": "track not found",
  "кадр не найден": "scene not found",
  "референс не найден": "reference not found",
  "персонаж не найден": "character not found",
  "исходный персонаж не найден": "the source character was not found",
  "атрибут не найден": "attribute not found",
  "фото не найдено": "photo not found",
  "аудио не найдено": "audio not found",
  "файл листа не найден": "the storyboard sheet file was not found",
  "неверный логин или пароль": "wrong login or password",
  "неверный пароль": "wrong password",
  "у аккаунта уже есть логин": "this account already has a login",
  "введи логин": "type a login",
  "пароль от 6 символов": "the password must be 6 characters or more",
  "логин занят": "that login is taken",
  "поддерживаются jpg/png/webp": "jpg / png / webp only",
  "нельзя удалить последний проект": "you cannot delete the last project",
  "сначала загрузи хотя бы один трек": "upload at least one track first",
  "это сингл — трек может быть только один": "this is a single — it can hold only one track",
  "сначала сгенерируй общий сюжет проекта (или включи «без сюжета»)":
    "write the project story first (or switch on “no story”)",
  "сначала сгенерируй лист раскадровки": "generate the storyboard sheet first",
  "сначала сгенерируй раскадровку трека": "generate the storyboard of the track first",
  "сначала сгенерируй раскадровку": "generate the storyboard first",
  "у трека нет сцен": "the track has no scenes",
  "у сцены пуст промпт первого кадра": "the first frame prompt of the scene is empty",
  "сцена короткая — промежуточные кадры не нужны":
    "the scene is too short — it needs no in-between frames",
  "сначала сгенерируй кадры сцены — референсом идёт первый кадр":
    "generate the scene frames first — the first frame goes in as reference",
  "сначала сгенерируй кадры сцены": "generate the scene frames first",
  "сначала сгенерируй видео сцены": "generate the scene video first",
  "нет утверждённых сцен с видео": "no approved scenes with video",
  "у всех сцен кадры уже готовы": "every scene already has its frames",
  "нет сцен с кадрами без видео": "no scenes with frames and without video",
  "у трека нет аудио — загрузи дорожку": "the track has no audio — upload the file",
  "не выбран стиль клипа — выбери пресет на карточке трека":
    "no clip style — pick a preset on the track card",
  "в проекте нет персонажей — добавь нового или клонируй из базы":
    "the project has no characters — add one or clone it from the library",
  "супергенерация уже идёт": "the one-click clip is already running",
  "нужно описание персонажа": "the character needs a description",
  "укажи реквизиты для выплаты": "fill in your payout details",
  "сначала подключи партнёрку": "join the affiliate program first",
  "сумма не похожа на число": "that amount is not a number",
  "доступных к выплате начислений пока нет": "nothing is available for payout yet",
  "заявка на эти деньги уже создана — обнови страницу":
    "a request for this money already exists — reload the page",
  "только для админа": "admins only",
  "заявка не найдена": "request not found",
  "не удалось выдать промокод, попробуй ещё раз":
    "could not issue a promo code, try again",
  "вход через Telegram не настроен": "Telegram sign-in is not set up",
  "вход через Google не настроен": "Google sign-in is not set up",
  "вход через Яндекс не настроен": "Yandex sign-in is not set up",
};

// ────────── язык: выбор, хранение, переключение ──────────
let LANG = "en";
const langHooks = [];

function detectLang() {
  try {
    const saved = localStorage.getItem(LANG_KEY);
    if (LANGS.includes(saved)) return saved;
  } catch (e) { /* приватный режим — идём дальше по браузеру */ }
  const list = (navigator.languages && navigator.languages.length)
    ? navigator.languages : [navigator.language || ""];
  for (const raw of list) {
    const code = String(raw || "").toLowerCase().slice(0, 2);
    if (code === "ru") return "ru";
    if (code === "en") return "en";
  }
  return "en";
}

// Путь в словаре: возвращает значение как есть (строка, массив, объект).
// Нет ключа в текущем языке — пробуем английский, потом отдаём "".
function tRaw(path, lang = LANG) {
  const walk = (root) => String(path || "").split(".").reduce(
    (acc, key) => (acc && acc[key] !== undefined ? acc[key] : undefined), root);
  const v = walk(I18N[lang] || I18N.en);
  if (v !== undefined) return v;
  const fb = lang === "en" ? undefined : walk(I18N.en);
  return fb === undefined ? "" : fb;
}

function tHas(path) {
  return tRaw(path) !== "";
}

// Подстановка {name} из vars. Значения подставляются как есть — вызывающий
// сам решает, экранировать их или нет (в разметке — обязательно).
function tFill(tpl, vars) {
  if (!vars) return String(tpl);
  return String(tpl).replace(/\{(\w+)\}/g, (m, k) => (vars[k] !== undefined ? String(vars[k]) : m));
}

function t(path, vars) {
  const v = tRaw(path);
  return typeof v === "string" ? tFill(v, vars) : "";
}

// Формы множественного числа: [1, 2–4, 5+]. Английскому хватает двух, но
// массив всегда из трёх — так словарь одинаковый для любого языка.
function tPlural(n, forms, lang = LANG) {
  const f = Array.isArray(forms) ? forms : [String(forms), String(forms), String(forms)];
  const num = Math.abs(Number(n) || 0);
  try {
    const cat = new Intl.PluralRules(lang === "ru" ? "ru-RU" : "en-US").select(num);
    if (cat === "one") return f[0];
    if (cat === "few") return f[1];
    return f[2];
  } catch (e) { /* без Intl — ручное правило ниже */ }
  const abs = num % 100, last = abs % 10;
  if (abs > 10 && abs < 20) return f[2];
  if (last > 1 && last < 5) return f[1];
  if (last === 1) return f[0];
  return f[2];
}

function tLocale(lang = LANG) {
  return lang === "ru" ? "ru-RU" : "en-US";
}

// Числа: 2400 → «2,400» в английском и «2 400» в русском.
function tNum(n) {
  const num = Number(n) || 0;
  try { return new Intl.NumberFormat(tLocale()).format(num); }
  catch (e) { return String(num); }
}

// ────────── разметка: data-i18n* ──────────
const I18N_ATTRS = [
  ["i18nPh", "placeholder"],
  ["i18nTitle", "title"],
  ["i18nAlt", "alt"],
  ["i18nAria", "aria-label"],
  ["i18nContent", "content"],
  ["i18nValue", "value"],
];

function applyI18n(root = document) {
  const scope = root || document;
  const all = (sel) => Array.from(scope.querySelectorAll(sel))
    .concat(scope.matches && scope.matches(sel) ? [scope] : []);

  all("[data-i18n]").forEach((el) => {
    const v = tRaw(el.dataset.i18n);
    if (typeof v !== "string" || !v) return;
    el.textContent = v;
  });
  all("[data-i18n-html]").forEach((el) => {
    const v = tRaw(el.dataset.i18nHtml);
    if (typeof v === "string" && v) el.innerHTML = v;
  });
  for (const [prop, attr] of I18N_ATTRS) {
    all(`[data-${prop.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase())}]`).forEach((el) => {
      const v = tRaw(el.dataset[prop]);
      if (typeof v === "string" && v) el.setAttribute(attr, v);
    });
  }
}

// Кнопки EN/RU: подсветка активного языка у всех переключателей сразу.
function syncLangSwitches(root = document) {
  Array.from((root || document).querySelectorAll("[data-lang]")).forEach((b) => {
    const on = b.dataset.lang === LANG;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

// app.js вешает сюда перерисовку студии и лендинга.
function onLangChange(fn) {
  if (typeof fn === "function") langHooks.push(fn);
}

function setLang(lang, opts = {}) {
  const next = LANGS.includes(lang) ? lang : "en";
  const changed = next !== LANG;
  LANG = next;
  try { localStorage.setItem(LANG_KEY, LANG); } catch (e) { /* приватный режим */ }
  document.documentElement.setAttribute("lang", LANG);
  applyI18n(document);
  syncLangSwitches();
  if (changed || opts.force) langHooks.forEach((fn) => { try { fn(LANG); } catch (e) { /* хук не должен ронять переключение */ } });
}

// Первичная установка языка — до первой отрисовки чего бы то ни было.
LANG = detectLang();
document.documentElement.setAttribute("lang", LANG);

// Клик по любому переключателю языка, где бы он ни был отрисован.
document.addEventListener("click", (e) => {
  const btn = e.target.closest && e.target.closest("[data-lang]");
  if (!btn) return;
  e.preventDefault();
  setLang(btn.dataset.lang);
});
