from flask import Blueprint, jsonify, current_app
from utils.music_processing_utils import compute_chromagram_from_filename
import os
music_processing = Blueprint('music_processing_routes', __name__)

@music_processing.route('/process-audio', methods=['POST'])
def process_audio():

    uploads_folder = os.path.join(current_app.root_path, 'uploads')

    wav_filename = 'FMP_C5_F01_Beatles_LetItBe-mm1-4_Original.wav'
    fn_wav = os.path.join(uploads_folder, wav_filename)

    if not os.path.exists(fn_wav):
        return jsonify({"error": "Audio file not found"}), 404

    N = 4096
    H = 2048
    X_STFT, Fs_X, x, Fs, x_dur = compute_chromagram_from_filename(fn_wav, N=N, H=H, gamma=0.1, version='STFT')

    chromagram_list = X_STFT.tolist()

    response = {
        "chromagram": chromagram_list,
        "Fs_X": Fs_X,
        "x_dur": x_dur
    }

    return jsonify(response)