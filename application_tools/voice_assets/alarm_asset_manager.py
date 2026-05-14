"""
SCADA Voice Asset Manager
=========================
Generates and tests all voice alarm WAV files.

Usage:
  python application_tools/voice_assets/alarm_asset_manager.py              (generate all)
  python application_tools/voice_assets/alarm_asset_manager.py --test       (interactive test menu)
  python application_tools/voice_assets/alarm_asset_manager.py --custom     (generate + test custom phrase)
"""
import os
import sys
import time
import winsound
import pyttsx3

ASSETS_DIR = os.path.dirname(os.path.abspath(__file__))

CORE_PHRASES = [
    "HIGH PRESSURE",
    "LOW PRESSURE",
    "HIGH WATER LEVEL",
    "LOW WATER LEVEL",
    "emergency initiated",
    "emergency reset initiated",
    "heater on",
    "heater off",
    "motor on",
    "motor off",
    "alarm cleared",
    "and",
]

def _get_engine():
    engine = pyttsx3.init()
    engine.setProperty('rate', 150)
    voices = engine.getProperty('voices')
    if voices:
        print(f"Using voice: {voices[0].name}")
        engine.setProperty('voice', voices[0].id)
    return engine

def _phrase_to_path(phrase):
    filename = phrase.replace(" ", "_").lower() + ".wav"
    return os.path.join(ASSETS_DIR, filename)


# -----------------------------------------------
# GENERATE
# -----------------------------------------------

def generate_all(force=False):
    """Generates WAV files for all core alarm phrases."""
    engine = _get_engine()
    print(f"\nSaving WAV files to: {ASSETS_DIR}\n")

    queued = 0
    for phrase in CORE_PHRASES:
        path = _phrase_to_path(phrase)
        if force or not os.path.exists(path) or os.path.getsize(path) == 0:
            engine.save_to_file(phrase, path)
            print(f"  Queued: {os.path.basename(path)}")
            queued += 1
        else:
            print(f"  Exists: {os.path.basename(path)}  ({os.path.getsize(path)} bytes)")

    if queued > 0:
        engine.runAndWait()
        time.sleep(0.5)

    # Verify
    print("\n--- Verification ---")
    all_ok = True
    for phrase in CORE_PHRASES:
        path = _phrase_to_path(phrase)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            print(f"  [OK]   {os.path.basename(path)}  ({os.path.getsize(path)} bytes)")
        else:
            print(f"  [FAIL] {os.path.basename(path)}")
            all_ok = False

    if all_ok:
        print(f"\n[OK] All {len(CORE_PHRASES)} voice assets ready.")
    else:
        print("\n[FAIL] Some files failed. Check TTS engine.")
    return all_ok


def generate_single(phrase):
    """Generates a single WAV file for a phrase."""
    engine = _get_engine()
    path = _phrase_to_path(phrase)
    print(f"Generating: {phrase} -> {os.path.basename(path)}")
    engine.save_to_file(phrase, path)
    engine.runAndWait()
    time.sleep(0.5)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        print(f"  [OK] {os.path.basename(path)}  ({os.path.getsize(path)} bytes)")
    else:
        print(f"  [FAIL] Could not create {os.path.basename(path)}")
    return path


# -----------------------------------------------
# TEST
# -----------------------------------------------

def test_alarm(phrase, freq=1000, duration=2000):
    """Tests the Beep-Voice-Beep pattern for a given phrase."""
    path = _phrase_to_path(phrase)

    if not os.path.exists(path):
        print(f"Asset missing, generating first...")
        generate_single(phrase)

    print(f"\n--- TESTING: {phrase} ---")
    print(f"  Step 1: Beep ({duration}ms @ {freq}Hz)")
    winsound.Beep(freq, duration)

    print(f"  Step 2: Voice -> {os.path.basename(path)}")
    winsound.PlaySound(path, winsound.SND_FILENAME)

    print(f"  Step 3: Beep ({duration}ms @ {freq}Hz)")
    winsound.Beep(freq, duration)
    print("--- DONE ---\n")


def interactive_test():
    """Interactive menu to test specific alarms."""
    print("\nAvailable Alarms:")
    for i, a in enumerate(CORE_PHRASES):
        print(f"  {i+1}. {a}")

    idx = int(input("\nSelect alarm number: ")) - 1
    if 0 <= idx < len(CORE_PHRASES):
        freq = input("Beep frequency (default 1000): ").strip()
        freq = int(freq) if freq else 1000
        dur = input("Beep duration ms (default 2000): ").strip()
        dur = int(dur) if dur else 2000
        test_alarm(CORE_PHRASES[idx], freq=freq, duration=dur)
    else:
        print("Invalid selection.")


def custom_phrase():
    """Generate and test a custom phrase."""
    phrase = input("Enter phrase to say: ")
    freq = input("Beep frequency (default 1000): ").strip()
    freq = int(freq) if freq else 1000
    dur = input("Beep duration ms (default 2000): ").strip()
    dur = int(dur) if dur else 2000
    generate_single(phrase)
    test_alarm(phrase, freq=freq, duration=dur)


# -----------------------------------------------
# MAIN
# -----------------------------------------------

if __name__ == "__main__":
    args = sys.argv[1:]

    if "--test" in args:
        interactive_test()
    elif "--custom" in args:
        custom_phrase()
    else:
        # Default: generate all assets
        generate_all()
