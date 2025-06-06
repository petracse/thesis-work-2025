import numpy as np
import librosa
import soundfile as sf
import os
import librosa
from madmom.audio.chroma import DeepChromaProcessor
from hmmlearn import hmm
import tempfile
import shutil
import yt_dlp

def normalize_feature_sequence(X, norm='2', threshold=0.0001, v=None):
    return []

def compute_chromagram_from_filename(fn_wav, Fs=22050, N=4096, H=2048, gamma=None, version='STFT', norm='2'):
    return [], [], [], [], []

def load_hmm_parameters(folder):
    means = np.load(os.path.join(folder, 'means.npy'))
    covariances = np.load(os.path.join(folder, 'covariances.npy'))
    transmat = np.load(os.path.join(folder, 'transmat.npy'))
    startprob = np.load(os.path.join(folder, 'startprob.npy'))
    with open(os.path.join(folder, 'chord_list.txt'), 'r', encoding='utf-8') as f:
        idx_to_chord = {}
        for line in f:
            idx, chord = line.strip().split('\t')
            idx_to_chord[int(idx)] = chord
    return means, covariances, transmat, startprob, idx_to_chord

def build_hmm(means, covariances, transmat, startprob):
    n_components = means.shape[0]
    model = hmm.GaussianHMM(
        n_components=n_components,
        covariance_type="full",
        init_params="",
        params=""
    )
    model.means_ = means
    model.covars_ = covariances
    model.transmat_ = transmat
    model.startprob_ = startprob
    return model

def process_music_file_for_chords_deepchroma(hmm_folder, yt_url, is_youtube, song_path, expected_sr=44100):
    """
    Akkordfelismerés DeepChroma + HMM alapján, opcionális YouTube letöltéssel.

    song_path: helyi fájl elérési útja (ha nem YouTube)
    hmm_folder: HMM paraméterek mappája
    yt_url: YouTube URL (ha is_youtube True)
    is_youtube: bool, letöltsön-e YouTube-ról
    expected_sr: elvárt mintavételezési frekvencia (default: 44100)
    """

    cleanup_temp = False
    temp_dir = None

    try:
        if is_youtube:
            import yt_dlp
            temp_dir = tempfile.mkdtemp()
            audio_path = os.path.join(temp_dir, 'audio')
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': audio_path,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'wav',
                    'preferredquality': '192',
                }],
                'quiet': True,
                'noplaylist': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([yt_url])
            # Mindig 44100 Hz-en olvassuk be
            y, sr = librosa.load(audio_path + ".wav", sr=expected_sr, mono=True)
            cleanup_temp = True
        else:
            y, sr = sf.read(song_path, dtype='float32')
            if y.ndim > 1:
                y = y.mean(axis=1)
            if sr != expected_sr:
                y = librosa.resample(y, orig_sr=sr, target_sr=expected_sr)
                sr = expected_sr

        tuning = librosa.estimate_tuning(y=y, sr=sr)  # félhangban [-0.5, 0.5)
        if abs(tuning) >= 0.10:  # 10 cent = 0.10 félhang
            y = librosa.effects.pitch_shift(y, sr=sr, n_steps=-tuning)  # visszaállítás standardra

        chroma_hop_length = sr // 10
        dcp = DeepChromaProcessor()
        chroma_orig = dcp(y)

        # Beat tracking
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        beat_frame_indices = librosa.time_to_frames(
            beat_times,
            sr=sr,
            hop_length=chroma_hop_length
        )

        beat_chroma = []
        for i in range(len(beat_frame_indices) - 1):
            start = beat_frame_indices[i]
            end = beat_frame_indices[i+1]
            if end > start:
                frames = chroma_orig[start:end]
                if frames.shape[0] == 0:
                    avg = np.zeros(chroma_orig.shape[1])
                else:
                    avg = np.mean(frames, axis=0)
                norm = avg / (np.sum(avg) + 1e-8)
                lognorm = np.log1p(norm)
                beat_chroma.append(lognorm)
            else:
                avg = chroma_orig[start]
                norm = avg / (np.sum(avg) + 1e-8)
                lognorm = np.log1p(norm)
                beat_chroma.append(lognorm)
        beat_chroma = np.array(beat_chroma)

        # HMM betöltése
        means, covariances, transmat, startprob, idx_to_chord = load_hmm_parameters(hmm_folder)
        model = build_hmm(means, covariances, transmat, startprob)

        # Predikció
        logprob, state_sequence = model.decode(beat_chroma)
        predicted_chords = [idx_to_chord[s] for s in state_sequence]

        # Időpont - akkord párok
        chords_by_time = {float(f"{t:.3f}"): chord for t, chord in zip(beat_times[:-1], predicted_chords)}
        chords_by_time = merge_consecutive_chords(chords_by_time)
        chords_by_time = simplify_chords_dict(chords_by_time)
        bpm = estimate_bpm_fourier(beat_times)

        return chords_by_time, bpm

    except Exception as e:
        print(f"Hiba a(z) {song_path if not is_youtube else yt_url} feldolgozásakor: {str(e)}")
        return {}, None

    finally:
        # Ideiglenes könyvtár törlése, ha letöltöttünk
        if cleanup_temp and temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


def merge_consecutive_chords(chords_by_time):
    merged_chords = {}
    previous_chord = None
    for time, chord in sorted(chords_by_time.items()):
        if chord != previous_chord:
            merged_chords[time] = chord
            previous_chord = chord
    return merged_chords

def estimate_bpm_fourier(beat_times, dt=0.05):
    if len(beat_times) < 2:
        return 0.0

    # Időtartomány
    t_start = beat_times[0]
    t_end = beat_times[-1]
    duration = t_end - t_start
    if duration <= 0:
        return 0.0

    # Időrács
    t = np.arange(t_start, t_end, dt)
    impulse = np.zeros_like(t)
    beat_indices = np.searchsorted(t, beat_times)
    # Vigyázat: lehet, hogy néhány index kilóg, ezért csak a tartományon belülieket állítjuk 1-re
    beat_indices = beat_indices[beat_indices < len(impulse)]
    impulse[beat_indices] = 1

    # DC komponens eltávolítása
    impulse = impulse - np.mean(impulse)

    # FFT
    spectrum = np.abs(np.fft.rfft(impulse))
    freqs = np.fft.rfftfreq(len(impulse), d=dt)

    # DC komponens kihagyása
    peak_idx = np.argmax(spectrum[1:]) + 1
    peak_freq = freqs[peak_idx]
    bpm = peak_freq * 60

    return bpm

def simplify_chord_name(chord):
    """
    Egyszerűsíti az akkord nevét:
    - 'A#:maj' -> 'A#'
    - 'A#:min' -> 'A#m'
    """
    if chord.endswith(':maj'):
        return chord[:-4]
    elif chord.endswith(':min'):
        return chord[:-4] + 'm'
    else:
        return chord

def simplify_chords_dict(chords_by_time):
    """
    chords_by_time: {időpont: akkord_név, ...}
    Visszaad: ugyanilyen dict, de egyszerűsített nevekkel.
    """
    return {time: simplify_chord_name(chord) for time, chord in chords_by_time.items()}
