# -*- coding: utf-8-*-
"""
    The Mic class handles all interactions with the microphone and speaker.
"""
import logging
import tempfile
import wave
import pyaudio
import client.jasperpath as jasperpath

import webrtcvad
import collections
import sys
import signal
from array import array
import time

RATE = 16000

gotOneSentence = False
leaveRecord = False


def handle_int(sig, chunk):
    global leaveRecord, gotOneSentence
    leaveRecord = True
    gotOneSentence = True


def normalize(snd_data):
    "Average the volume out"
    MAXIMUM = 32767  # 16384
    times = float(MAXIMUM) / max(abs(i) for i in snd_data)
    r = array('h')
    for i in snd_data:
        r.append(int(i * times))
    return r


class Mic:

    speechRec = None
    speechRec_persona = None

    def __init__(self, speaker, passive_stt_engine, active_stt_engine):
        """
        Initiates the pocketsphinx instance.

        Arguments:
        speaker -- handles platform-independent audio output
        passive_stt_engine -- performs STT while Jasper is in passive listen
                              mode
        acive_stt_engine -- performs STT while Jasper is in active listen mode
        """
        self._logger = logging.getLogger(__name__)
        self.speaker = speaker
        self.passive_stt_engine = passive_stt_engine
        self.active_stt_engine = active_stt_engine
        self._logger.info("Initializing PyAudio. ALSA/Jack error messages " +
                          "that pop up during this process are normal and " +
                          "can usually be safely ignored.")
        self._audio = pyaudio.PyAudio()
        self._logger.info("Initialization of PyAudio completed.")

    def __del__(self):
        self._audio.terminate()

    def passiveListen(self, PERSONA):
        """
        Listens for PERSONA in everyday sound. Times out after LISTEN_TIME, so
        needs to be restarted.
        """
        transcribed = self.listenVoice(False)
        if any(PERSONA in phrase for phrase in transcribed):
            return (True, PERSONA)

        return (False, transcribed)

    def activeListen(self, THRESHOLD=None, LISTEN=True, MUSIC=False):
        """
            Records until a second of silence or times out after 12 seconds

            Returns the first matching string or None
        """

        options = self.listenVoice(True)
        if options:
            return options[0]

    def listenVoice(self, ACTIVE=True):
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        CHUNK_DURATION_MS = 30       # supports 10, 20 and 30 (ms)
        PADDING_DURATION_MS = 1500   # 1 sec jugement
        CHUNK_SIZE = int(RATE * CHUNK_DURATION_MS / 1000)   # chunk to read
        NUM_PADDING_CHUNKS = int(PADDING_DURATION_MS / CHUNK_DURATION_MS)
        NUM_WINDOW_CHUNKS = int(400 / CHUNK_DURATION_MS)    # 400ms/30ms
        NUM_WINDOW_CHUNKS_END = NUM_WINDOW_CHUNKS * 2

        global leaveRecord, gotOneSentence

        vad = webrtcvad.Vad(1)

        if ACTIVE:
            self.speaker.play(jasperpath.data('audio', 'beep_hi.wav'))

        # prepare recording stream
        stream = self._audio.open(format=FORMAT,
                                  channels=CHANNELS,
                                  rate=RATE,
                                  input=True,
                                  start=False,
                                  frames_per_buffer=CHUNK_SIZE)
        signal.signal(signal.SIGINT, handle_int)

        while not leaveRecord:
            ring_buffer = collections.deque(maxlen=NUM_PADDING_CHUNKS)
            triggered = False
            ring_buffer_flags = [0] * NUM_WINDOW_CHUNKS
            ring_buffer_index = 0

            ring_buffer_flags_end = [0] * NUM_WINDOW_CHUNKS_END
            ring_buffer_index_end = 0
            raw_data = array('h')
            index = 0
            start_point = 0
            StartTime = time.time()
            print("* recording: ")
            stream.start_stream()

            while not gotOneSentence and not leaveRecord:
                chunk = stream.read(CHUNK_SIZE)
                # add WangS
                raw_data.extend(array('h', chunk))
                index += CHUNK_SIZE
                TimeUse = time.time() - StartTime

                active = vad.is_speech(chunk, RATE)

                if ACTIVE:
                    sys.stdout.write('I' if active else '_')
                ring_buffer_flags[ring_buffer_index] = 1 if active else 0
                ring_buffer_index += 1
                ring_buffer_index %= NUM_WINDOW_CHUNKS

                ring_buffer_flags_end[ring_buffer_index_end] = \
                    1 if active else 0
                ring_buffer_index_end += 1
                ring_buffer_index_end %= NUM_WINDOW_CHUNKS_END

                # start point detection
                if not triggered:
                    ring_buffer.append(chunk)
                    num_voiced = sum(ring_buffer_flags)
                    if num_voiced > 0.8 * NUM_WINDOW_CHUNKS:
                        sys.stdout.write('[OPEN]')
                        triggered = True
                        start_point = index - CHUNK_SIZE * 20  # start point
                        # voiced_frames.extend(ring_buffer)
                        ring_buffer.clear()
                # end point detection
                else:
                    # voiced_frames.append(chunk)
                    ring_buffer.append(chunk)
                    num_unvoiced = NUM_WINDOW_CHUNKS_END \
                        - sum(ring_buffer_flags_end)
                    if num_unvoiced > 0.90 * NUM_WINDOW_CHUNKS_END \
                            or TimeUse > 10:
                        sys.stdout.write('[CLOSE]')
                        triggered = False
                        gotOneSentence = True

                sys.stdout.flush()

            sys.stdout.write('\n')

            print("* done recording")
            gotOneSentence = False
            if ACTIVE:
                self.speaker.play(jasperpath.data('audio', 'beep_lo.wav'))

            # write to file
            raw_data.reverse()
            for index in range(start_point):
                raw_data.pop()
            raw_data.reverse()
            raw_data = normalize(raw_data)

            stream.stop_stream()
            stream.close()

            # save the audio data
            with tempfile.SpooledTemporaryFile(mode='w+b') as f:
                wav_fp = wave.open(f, 'wb')
                wav_fp.setnchannels(1)
                wav_fp.setsampwidth(pyaudio.get_sample_size(pyaudio.paInt16))
                wav_fp.setframerate(RATE)
                wav_fp.writeframes(raw_data)
                wav_fp.close()
                f.seek(0)
                if ACTIVE:
                    return self.active_stt_engine.transcribe(f)
                else:
                    return self.passive_stt_engine.transcribe(f)
            leaveRecord = True

        # exit
        stream.close()

    def say(self, phrase,
            OPTIONS=" -vdefault+m3 -p 40 -s 160 --stdout > say.wav"):
        # alter phrase before speaking
        self.speaker.say(phrase)
