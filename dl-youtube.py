#!/usr/bin/env python
# -*- coding:utf-8 mode:python -*-

import csv
import datetime
import logging
import os
import pprint
import re
import shutil
import sys
import traceback

# TODO
# - Support download not from youtube (done)
# - Readme for 'user' and 'developer' (done)
# - input csv with 'av', 'v', 'a'
# - generate standalone executable with nuitka
#   $ python -m nuitka --standalone --show-progress --show-scons dl-youtube.py


class DLException(Exception): pass
class DLReqError(DLException): pass
class DLCommandError(DLException): pass
class DLFolderError(DLException): pass
class DLFolderNotFound(DLException): pass
class DLInvalidOption(DLException): pass
class DLYoutubeDLError(DLException): pass


# check python version >= 3.6
if sys.version_info < (3, 6):
    try:
        raise DLReqError('\nMust use python ver >= 3.6')
    except:
        traceback.print_exc()
        sys.exit(1)

try:
    from mutagen.id3 import (APIC, ID3, LINK, TALB, TCON, TDAT, TIT2, TPE1, TPE2, TYER)
except ImportError:
    try:
        raise DLReqError('\nNeed mutagen library')
    except:
        traceback.print_exc()
        sys.exit(1)

try:
    import youtube_dl
except ImportError:
    try:
        raise DLReqError('\nNeed youtube-dl library')
    except:
        traceback.print_exc()
        sys.exit(1)

try:
    from ffmpeg_normalize import FFmpegNormalize
except ImportError:
    try:
        raise DLReqError('\nNeed ffmpeg-normalize library')
    except:
        traceback.print_exc()
        sys.exit(1)

# check executables
if shutil.which('ffmpeg') is None and shutil.which('avconv') is None:
    msg =  '\nNeither ffmpeg nor avconv is found!'
    msg += '\nPlease install at least either one:'
    msg += '\n    ffmpeg:   $ sudo apt install ffmpeg'
    msg += '\n    avconv:   $ sudo apt install avconv'
    raise DLReqError(msg)

DEBUG = False

__version_info__ = (0, 8, 1)
__version__ = '.'.join(str(c) for c in __version_info__)
__progname_version__ = __file__ + ' ' + __version__

# version history
# v0.8.1
# - support download other than youtube (video only)
# v0.8.0
# - initial release

class DLYoutube(object):
    '''
    Download from youtube, normalize and update ID3 tag
    '''
    PRJ_ROOT          = os.path.dirname(os.path.realpath(__file__))
    TEMP_FOLDER       = os.path.join(PRJ_ROOT, 'tmp')
    TEMP_VIDEO_FOLDER = os.path.join(TEMP_FOLDER, 'video')
    TEMP_AUDIO_FOLDER = os.path.join(TEMP_FOLDER, 'audio')
    OUTPUT_FOLDER     = os.path.join(PRJ_ROOT, 'output')
    COVER_FOLDER      = os.path.join(PRJ_ROOT, 'cover')
    # same as frame name in ID3
    DLINK        = 'LINK'  # link information
    ALBUMARTIST  = 'TPE2'  # 'albumartist'
    ALBUM        = 'TALB'  # 'album'
    TITLE        = 'TIT2'  # 'title'
    ARTIST       = 'TPE1'  # 'artist'
    GENRE        = 'TCON'  # 'genre'
    DATE         = 'TDAT'  # 'date'
    YEAR         = 'TYER'  # year of recording
    PICTURE      = 'picture'
    # this is input csv format sequence
    INPUT_CSV_HEADER = (DLINK, ALBUMARTIST, ALBUM, TITLE, ARTIST, GENRE, YEAR, PICTURE)
    #
    def __init__(self, **kwargs):
        self.inputList    = None
        self.outputFolder = None
        self.logger       = None
        self.verbose      = None
        #
        self.getaudio     = not kwargs.get('videoonly', True)
        self.getvideo     = not kwargs.get('audioonly', True)
        #
        self.verbose      = self.set_verbosity(kwargs.get('verbose', logging.NOTSET))  # integer
        #
        folderoutput = kwargs.get('folderoutput', self.OUTPUT_FOLDER)
        if folderoutput is not None:
            self.outputFolder = os.path.abspath(folderoutput)
        else:
            self.outputFolder = self.OUTPUT_FOLDER
        #
        if not os.path.isdir(self.outputFolder):
            raise DLFolderNotFound('Output Folder Not Exist: {}'.format(self.outputFolder))
        #
        inputlist    = kwargs.get('inputlist')
        try:
            self.inputList = self.parse_input_list(os.path.abspath(inputlist))
        except FileNotFoundError as e:
            print('Input file not found: {}'.format(e))
            raise
        except Exception as e:
            print('Failed open input file {}. Exit!'.format(e))
            raise
            #
        try:
            os.makedirs(self.TEMP_FOLDER)
        except OSError:
            if not os.path.isdir(self.TEMP_FOLDER):
                raise DLFolderError('Create temporary folder failed: {}'.format(self.TEMP_FOLDER))
        #
        coverfolder = kwargs.get('coverfolder', self.COVER_FOLDER)
        if coverfolder is not None:
            self.coverFolder = os.path.abspath(coverfolder)
        else:
            self.coverFolder = self.COVER_FOLDER
        #
        try:
            os.makedirs(self.TEMP_VIDEO_FOLDER)
        except OSError:
            if not os.path.isdir(self.TEMP_VIDEO_FOLDER):
                raise DLFolderError('Create temporary folder failed {}'.format(self.TEMP_VIDEO_FOLDER))
        #
        try:
            os.makedirs(self.TEMP_AUDIO_FOLDER)
        except OSError:
            if not os.path.isdir(self.TEMP_AUDIO_FOLDER):
                raise DLFolderError('Create temporary folder failed {}'.format(self.TEMP_AUDIO_FOLDER))
        # setup logger
        formatter = logging.Formatter('%(funcName)s:%(lineno)d %(message)s')
        self.logger = logging.getLogger('DLYoutube')
        self.logger.setLevel(self.verbose)
        # output log to console
        consoleHandler = logging.StreamHandler(sys.stdout)
        consoleHandler.setFormatter(formatter)
        self.logger.addHandler(consoleHandler)
        # output log to file
        now = datetime.datetime.now()
        logFile = os.path.join(self.TEMP_FOLDER, 'dl-youtube_{}.log'.format(now.strftime('%Y%m%d_%H%M%S')))
        fileHandler = logging.FileHandler(logFile)
        fileHandler.setFormatter(formatter)
        self.logger.addHandler(fileHandler)
        # check binary executables, etc
        self.logger.info('INFO:: Generated by ' + os.path.basename(__file__) + ' ' + __version__ + ' on ' + now.strftime('%d-%b-%Y %H:%M:%S'))
        self.logger.info('INFO:: Temporary video folder: ' + self.TEMP_VIDEO_FOLDER)
        self.logger.info('INFO:: Temporary audio folder: ' + self.TEMP_AUDIO_FOLDER)
        self.logger.info('INFO:: Output folder         : ' + self.outputFolder)
    #
    def parse_input_list(self, inputcsvfile):
        lod = []
        def decomment(csvfile):
            for row in csvfile:
                raw = row.split('#')[0].strip()
                if raw: yield raw
        with open(inputcsvfile, 'rt') as f:
            for od in csv.DictReader(decomment(f), fieldnames=self.INPUT_CSV_HEADER):
                lod.append(od)
        #
        return lod
    #
    def set_verbosity(self, verbose):
        """
        Set verbosity.
        usage:  DEBUG for developer, and INFO for user
        ------------------------------------------
          Level        Numeric value  User options
        ------------------------------------------
         CRITICAL           50
         ERROR              40
         WARNING            30
         INFO               20            -v
         DEBUG              10            -vv
         NOTSET              0
        -------------------------------------------
        """
        if verbose >= 2:        # -vv
            return logging.DEBUG
        elif verbose == 1:      # -v
            return logging.INFO
        else:
            return logging.NOTSET
    #
    def isYoutubeLink(self, link):
        return True if 'youtube' in link else False

    #
    def ydl_hook(self, d):
        if d['status'] == 'finished':
            msg = 'Downloaded {}'.format(d['filename'])
            if 'downloaded_bytes' in d:
                msg += ' size: {} bytes'.format(d['downloaded_bytes'])
            #
            self.logger.info('INFO:: ' + msg)
        elif d['status'] == 'downloading':
            self.logger.debug('DEBUG:: Downloading {}, ETA: {} seconds'.format(d['filename'], d['eta']))
        elif d['status'] == 'error':
            self.logger.error('ERROR::')
    #
    def main(self):
        ydl_opts = {
            'keepvideo'     : True,
            'logger'        : self.logger,
            'noprogress'    : True,
            'progress_hooks': [self.ydl_hook]
        }
        #
        for od in self.inputList:
            dlink         = od[self.DLINK].strip().strip('"')       # download link
            albumartist   = od[self.ALBUMARTIST].strip().strip('"') # album artist
            album         = od[self.ALBUM].strip().strip('"')       # album name
            song          = od[self.TITLE].strip().strip('"')       # song name
            artist        = od[self.ARTIST].strip().strip('"')      # artist
            genre         = od[self.GENRE].strip().strip('"')       # genre
            # date          = od[self.DATE].strip().strip('"')      # year
            year          = od[self.YEAR].strip().strip('"')        # year
            cover         = od[self.PICTURE].strip().strip('"')     # cover picture

            # non-alphanumeric change to underscore
            albumartist_fname = re.sub('[^0-9a-zA-Z]+', '_', albumartist)
            albumartist_fpath = os.path.join(self.outputFolder, albumartist_fname)
            album_fname  = re.sub('[^0-9a-zA-Z]+', '_', album)
            album_fpath  = os.path.join(albumartist_fpath, album_fname)
            song_fname   = re.sub('[^0-9a-zA-Z]+', '_', song)       # title           (without file ext)
            song_fpath   = os.path.join(album_fpath, song_fname)    # title full path (without file ext)
            audio_tmp_fpath = os.path.join(self.TEMP_AUDIO_FOLDER, albumartist_fname + '-' + album_fname + '-' + song_fname)
            video_tmp_fpath = os.path.join(self.TEMP_VIDEO_FOLDER, albumartist_fname + '-' + album_fname + '-' + song_fname)
            # create folder: output/albumartist_fname/
            try:
                os.makedirs(albumartist_fpath)
            except OSError:
                if not os.path.isdir(albumartist_fpath):
                    continue
            # create folder: output/albumartist_fname/album_fname/
            try:
                os.makedirs(album_fpath)
            except OSError:
                if not os.path.isdir(album_fpath):
                    continue
            #
            isyoutube = self.isYoutubeLink(dlink)
            #
            try:
                # VIDEO (MKV)
                self.logger.info('')
                self.logger.info('INFO:: Processing Video "{}"'.format(song))
                if self.getvideo:
                    videotmpfpath = video_tmp_fpath + '.mkv' # output of YoutubeDL, as input for FFmpegNormalize
                    videofpath    = song_fpath + '.mkv'      # output of FFmpegNormalize
                    # 1. Download video. Input: youtube link, output: file in videotmpfpath folder
                    if os.path.isfile(videotmpfpath):
                        self.logger.debug('DEBUG:: Skip downloading: File "{}" already exist!'.format(videotmpfpath))
                    else:
                        self.logger.info('INFO:: Downloading video: {} ...'.format(song))
                        ydl_video_opts = {
                            'postprocessors' : [{
                                'key'           : 'FFmpegVideoConvertor',
                                'preferedformat': 'mkv'
                            }],
                            'outtmpl'        : video_tmp_fpath + '.%(ext)s'
                        }
                        #
                        if isyoutube:
                            ydl_video_opts.update({'format'         : 'bestvideo+bestaudio'})
                        #
                        self.logger.debug('DEBUG:: Downlink: {}'.format(dlink))
                        self.logger.debug('DEBUG:: Options: {}'.format({**ydl_opts, **ydl_video_opts}))
                        try:
                            with youtube_dl.YoutubeDL({**ydl_opts, **ydl_video_opts}) as ydl:
                                ydl.download([dlink])
                        except youtube_dl.utils.YoutubeDLError:
                            raise DLYoutubeDLError('Abort downloading Video "{}"'.format(song))
                        except Exception:
                            raise
                        # Delete existing normalized video
                        try:
                            os.remove(videofpath)
                        except OSError:
                            pass
                    # 2. Normalize video. Input video from videotmpfpath, output video to videofpath
                    if os.path.isfile(videofpath):
                        self.logger.debug('DEBUG:: Skip normalizing: Video file "{}" already exist!'.format(videofpath))
                    else:
                        self.logger.info('INFO:: Normalizing file {} ...'.format(videotmpfpath))
                        ffmpeg_normalize = FFmpegNormalize(
                            dual_mono        = True,
                            progress         = True,
                            audio_codec      = 'libmp3lame',   # -c:a libmp3lame
                            audio_bitrate    = '320k',         # -b:a 320k
                            target_level     = -14.0           # -t -14
                        )
                        ffmpeg_normalize.add_media_file(videotmpfpath, videofpath)
                        ffmpeg_normalize.run_normalization()
                        self.logger.info('INFO:: Normalizing done. Output file: {}'.format(videofpath))
                # AUDIO (MP3)
                self.logger.info('')
                self.logger.info('INFO:: Processing Audio "{}"'.format(song))
                if self.getaudio and isyoutube:
                    # 3. Download Audio
                    audiotmpfpath = audio_tmp_fpath + '.mp3'
                    audiofpath    = song_fpath + '.mp3'
                    if os.path.isfile(audiotmpfpath):
                        self.logger.debug('DEBUG:: Skip downloading: File "{}" already exist!'.format(audiotmpfpath))
                    else:
                        self.logger.info('INFO:: Downloading audio "{}" ...'.format(song))
                        ydl_audio_opts = {
                            'format'         : 'bestaudio',
                            'verbose'        : True,
                            'postprocessors' : [{
                                'key'             : 'FFmpegExtractAudio',
                                'preferredcodec'  : 'mp3',
                                'preferredquality': '320',
                                'nopostoverwrites': False
                            }],
                            'outtmpl'        : audio_tmp_fpath + '.%(ext)s'
                        }
                        pp = pprint.PrettyPrinter(indent=2)
                        self.logger.debug('DEBUG:: YoutubeDL Options: ' + pp.pformat({**ydl_opts, **ydl_audio_opts}))
                        try:
                            with youtube_dl.YoutubeDL({**ydl_opts, **ydl_audio_opts}) as ydl:
                                ydl.download([dlink])
                        except youtube_dl.utils.YoutubeDLError:
                            raise DLYoutubeDLError('Abort downloading Audio "{}"'.format(song))
                        except Exception:
                            raise
                        # 4. Update ID3 tag
                        self.logger.info('INFO:: Updating MP3 ID3 tag ...')
                        audio = ID3(audiotmpfpath)
                        audio[self.ALBUMARTIST] = TPE2(encoding=3, text=albumartist)
                        audio[self.ALBUM]       = TALB(encoding=3, text=album)
                        audio[self.TITLE]       = TIT2(encoding=3, text=song)
                        audio[self.ARTIST]      = TPE1(encoding=3, text=artist)
                        audio[self.GENRE]       = TCON(encoding=3, text=genre)
                        audio[self.YEAR]        = TYER(encoding=3, text=year)
                        audio[self.DATE]        = TDAT(encoding=3, text=year)
                        audio[self.DLINK]       = LINK(encoding=3, url=dlink)

                        try:
                            with open(os.path.join(self.coverFolder, cover), 'rb') as albumart:
                                audio['APIC'] = APIC(encoding=3, mime='image/jpg', type=3,
                                                    desc=u'Cover', data=albumart.read())
                        except Exception as e:
                            self.logger.debug('DEBUG:: Skipped album art file error: {}'.format(e))
                        #
                        try:
                            audio.save()
                            self.logger.info('INFO:: Audio ID3 Tag completed on file: {}'.format(audiotmpfpath))
                            self.logger.info('INFO::   Title        : ' + audio[self.TITLE].text[0])
                            self.logger.info('INFO::   Album        : ' + audio[self.ALBUM].text[0])
                            self.logger.info('INFO::   Album Artist : ' + audio[self.ALBUMARTIST].text[0])
                            self.logger.info('INFO::   Artist       : ' + audio[self.ARTIST].text[0])
                            self.logger.info('INFO::   Genre        : ' + audio[self.GENRE].text[0])
                            self.logger.info('INFO::   Year         : ' + audio[self.YEAR].text[0])
                            self.logger.info('INFO::   Date         : ' + audio[self.DATE].text[0])
                            self.logger.info('INFO::   Link         : ' + audio[self.DLINK].url)
                        except Exception:
                            self.logger.error('ERROR:: Error on saving ID3 tag!')
                        #
                        # Since we've updated the metadata, we need to normalize again.
                        # Hence, delete target
                        try:
                            os.remove(audiofpath)
                        except OSError:
                            pass
                    # 5. Normalize audio
                    if os.path.isfile(audiofpath):
                        self.logger.debug('DEBUG:: Skip normalizing: Audio file "{}" already exist!'.format(audiofpath))
                    else:
                        self.logger.info('INFO:: Normalizing file: {}'.format(audiotmpfpath))
                        ffmpeg_normalize = FFmpegNormalize(
                            dual_mono        = True,
                            progress         = True,
                            audio_codec      = 'libmp3lame',   # -c:a libmp3lame
                            audio_bitrate    = '320k',         # -b:a 320k
                            target_level     = -14.0           # -t -14
                        )
                        ffmpeg_normalize.add_media_file(audiotmpfpath, audiofpath)
                        ffmpeg_normalize.run_normalization()
                        self.logger.info('INFO:: Normalizing done. Output file: {}'.format(audiofpath))
                #
            except DLYoutubeDLError as e:
                self.logger.error('ERROR:: DLYoutubeDLError {}'.format(e))
            except Exception as e:
                self.logger.error('ERROR:: {}'.format(e))

if __name__ == "__main__":
    import argparse

    #
    def main(args):
        opts = { 'verbose'         : args.verbose,
                 'inputlist'       : args.inputlist,
                 'audioonly'       : args.audioonly,
                 'videoonly'       : args.videoonly,
                 'coverfolder'     : args.coverfolder,
                 'folderoutput'    : args.outputfolder}
        DLYoutube(**opts).main()

    # setup parser
    parser = argparse.ArgumentParser(description='Youtube Downloader and Stuffs')
    group01 = parser.add_mutually_exclusive_group()
    parser.add_argument('--version', action='version', version=__progname_version__)
    group01.add_argument('--audio-only', dest='audioonly', action='store_true', help='Download audio')
    group01.add_argument('--video-only', dest='videoonly', action='store_true', help='Download video')
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help='increase verbosity. Specify multiple times for increased diagnostic output.')
    parser.add_argument('-c', '--coverfolder', dest='coverfolder', default=None, help='Cover art folder')
    parser.add_argument('-i', '--inputlist', dest='inputlist', required=True, help='List input')
    parser.add_argument('-o', '--outputfolder', dest='outputfolder', default=None, help='Folder output')
    parser.set_defaults(func=main)
    #
    args = parser.parse_args()
    try:
        args.func(args)
    except AttributeError as e:     # neither optional nor positional argument supplied by user
        try:
            raise DLReqError(e)
        except:
            traceback.print_exc()
            parser.print_help()
            sys.exit(1)
    except DLException:
        try:
            raise
        except:
            traceback.print_exc()
            parser.print_help()
            sys.exit(1)
    #
    sys.exit(0)

# eof
