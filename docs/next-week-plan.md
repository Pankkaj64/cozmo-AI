# What I will improve in the next one week

These are the things I want to add or fix next. I have kept them in the order I plan to do them.

## 1. Show failures and progress in the frontend

Right now if something goes wrong in the backend (model not loaded, OCR not working), the UI
only shows one small line of text. It is easy to miss.

What I will do:

- Add a small status bar at the top. It will show: is the backend running, which models are
  loaded, which stage is running now ("reading spine 3 of 10"), how many frames are waiting.
- Show the last captured frame with the boxes drawn on it, so the user can see what was
  detected and what was missed while they are still standing in front of the shelf.
- If a frame fails, show the reason in red (for example "detector not installed") instead of
  hiding it in the guidance text.

Why: the reviewer will point the app at their own shelf. If something breaks, we should see it
immediately, not after the sweep.

## 2. Tell the user in the frontend: this is a book / this is not a book

Today the inventory only shows a list of books and items. The user cannot see why one box was
accepted as a book and another was not.

What I will do:

- Next to every detected box, show a small label: "book", "not a book (cup)", or "not sure".
  This comes from the second model (Qwen) which checks the crop without knowing the label.
- If it says "not a book", show what it thinks it is, and give one button to move it to items.
- If it says "not sure", keep it in the review list with the reason written in plain words.

Why: this is the main safety check against wrong items entering the claim, and right now it is
invisible to the user.

## 3. Replace the 2-second snapshots with proper video using MediaRecorder

Right now the browser takes one picture every 2 seconds. Anything the camera passes between two
pictures is never seen. Also, when the backend is busy with one frame, no new picture is taken
at all, so the real gap can be minutes.

What I will do:

- Use the browser `MediaRecorder` API to record the whole sweep as one video (WebM or MP4) and
  send it to the backend in small chunks while recording.
- On the backend, read the video and take frames from it at a fixed rate (for example 2 per
  second), and pick the sharpest frame for each shelf.
- Keep the live preview and the voice guidance exactly as they are now.
- Keep the video file as evidence, so every frame reference can point to a time in the video.

Why: the brief says "one continuous sweep". With video, nothing is missed between snapshots and
the agent can re-read a shelf from the recording without asking the user to go back.

## 4. Pricing in more detail

Right now a book shows one replacement price and one used price (and most are blank until the
eBay and Google Books keys are added). I want to show more about the price so the adjuster can
trust it.

What I will do:

- Add eBay and Google Books keys first, so real prices come in.
- For every book show: new price, used price, and the difference in percent (for example
  "used is 60% below new").
- Show how many second-hand copies are available right now and the lowest / median / highest
  price of them, with the links.
- Show if the price is from the local market or converted from another country, and the
  exchange rate and date used.
- Show a small history: if the same book was priced before (another sweep or the second-country
  comparison), show how much the price went up or down in percent.
- Keep the rule: if there is no source, the price stays blank and goes to the review list.

Why: pricing is 20% of the score, and the reviewer wants to see where every number came from.

## 5. Mobile-friendly layout

The brief says the user opens the app on a phone. Today the page is designed for a laptop
screen.

What I will do:

- One column layout on small screens: camera on top, the agent's message under it, then the
  inventory, then the transcript.
- Bigger buttons (Start, Stop, Capture now) that are easy to press with a thumb.
- Use the back camera by default on the phone (already done) and keep the screen awake during
  the sweep.
- Test on Chrome on Android and Safari on iPhone, including the microphone permission.

Why: a real sweep is done walking around a room with a phone, not sitting at a laptop.

## Order and time

| Day | Work |
| --- | --- |
| 1 | status bar, frame with boxes, errors in red |
| 2 | book / not a book labels and the move-to-items button |
| 3–4 | MediaRecorder video and frame extraction on the backend |
| 5 | price keys, price details and percentages |
| 6 | mobile layout and phone testing |
| 7 | one full sweep of my own room with all of this, fix what breaks |
