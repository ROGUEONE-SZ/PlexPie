# Room correction with REW

Rooms boost some bass notes and swallow others; basements are the worst. Room correction measures
what your room does and corrects it with PlexPie's equalizer. It takes about 30 minutes the first time.

> **Receiver with its own room calibration?** Onkyo AccuEQ, Denon/Marantz Audyssey, Yamaha YPAO and
> Sony DCAC do this automatically with the microphone in the box. Run that first; it's easier.
> Use this guide if you don't have one, or want finer control of the bass.

## What you need

- A **USB measurement microphone** with a calibration file, for example the miniDSP **UMIK-1**
  (about $100). A phone or laptop microphone isn't accurate enough for bass.
- A Mac, Windows or Linux computer with **[REW](https://www.roomeqwizard.com)** (Room EQ Wizard, free).
- About 30 minutes of quiet.

## 1. Get ready

1. On PlexPie, open **Settings → Sound** and turn the **Equalizer off**, so you measure the room as it is.
2. Install REW, plug in the microphone, and in REW open **Preferences → Soundcard**: choose the
   microphone as input and load its calibration file (download it from the maker with your mic's
   serial number).
3. On your computer, send its sound to PlexPie: on a Mac, choose your player in the AirPlay menu
   (Control Center → Sound); on Windows or Linux, pair with PlexPie over Bluetooth.
4. Put the microphone where your head usually is, pointing at the ceiling, at ear height.
5. Set the volume to a comfortable, fairly loud level (REW's level meter should show about 75–80 dB).

## 2. Measure

1. In REW, click **Measure**. Under **Timing reference**, choose **Use acoustic timing reference**
   (AirPlay and Bluetooth add a delay; this handles it).
2. Leave the sweep at 20 Hz to 20 kHz and click **Start**. You'll hear a rising tone.
3. Optional but better: measure again with the microphone a little to the left, right, front and back
   of your seat, then use **All SPL → Average the responses**.

## 3. Make the correction

1. Click **EQ**. Under **Equalizer**, choose **Generic**. Under **Target settings**, click
   **Set target level** (REW picks a sensible level).
2. Under **Filter tasks**, set **Match range** to **20 Hz – 300 Hz**. Correcting only the bass is safer
   and fixes most of what you hear in a room.
3. Set **Individual max boost** to **3 dB** and **Overall max boost** to **0 dB**. Cutting peaks works far
   better than filling dips.
4. Click **Match response to target**. REW calculates up to 20 filters; the green line shows the result.
5. Click **Export filter settings as text** (or **File → Export → Filter settings as text**) and choose
   the Equalizer APO format if asked.

## 4. Load it into PlexPie

1. Open **Settings → Sound** on your phone or computer, turn the **Equalizer on**, and choose
   **Pasted filters (AutoEQ or REW)**.
2. Open the exported file, copy everything, paste it into the box, and press **Save**.
   The curve under the box shows what the correction does.
3. Listen. If it sounds better, you're done. To compare, switch the equalizer off and on.

**Tip:** measure again with the equalizer on (repeat section 2) to check the result.
