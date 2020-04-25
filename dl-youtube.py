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
if shutil.which('ffmpeg') is None:
    msg =  '\nffmpeg is not found!'
    msg += '\nPlease install: $ sudo apt install ffmpeg'
    raise DLReqError(msg)

__version_info__ = (0, 8, 4)
__version__ = '.'.join(str(c) for c in __version_info__)
__progname_version__ = __file__ + ' ' + __version__

# version history
# v0.8.4
# - Download and embed English subtitle (only file subtitle, not stream) from YouTube video
# v0.8.3
# - Bugfix on youtube downloaded filename
# - Customize youtube-dl cache dir with option to delete it
# v0.8.2
# - Add first column on csv input as download type
# v0.8.1
# - support download other than youtube (video only)
# v0.8.0
# - initial release
#
# TODO
# - generate standalone executable with nuitka
#   $ python -m nuitka --standalone --show-progress --show-scons dl-youtube.py
# - add -v for youtube-dl

class DLYoutube(object):
    '''
    Download from youtube, normalize and update ID3 tag
    '''
    PRJ_ROOT          = os.path.dirname(os.path.realpath(__file__))
    OUTPUT_FOLDER     = os.path.join(PRJ_ROOT, 'output')
    COVER_FOLDER      = os.path.join(PRJ_ROOT, 'cover')
    TEMP_FOLDER       = os.path.join(PRJ_ROOT, 'tmp')
    # same as frame name in ID3
    # 'a': audio only [MP3]
    # 'v': video only [MKV: with it's audio, no separate mp3]
    # 'av': both audio [MP3] and video [MKV]
    DLTYPE       = 'DLTYPE'  # Download type
    DLINK        = 'LINK'    # link information
    ALBUMARTIST  = 'TPE2'    # 'albumartist'
    ALBUM        = 'TALB'    # 'album'
    TITLE        = 'TIT2'    # 'title'
    ARTIST       = 'TPE1'    # 'artist'
    GENRE        = 'TCON'    # 'genre'
    DATE         = 'TDAT'    # 'date'
    YEAR         = 'TYER'    # year of recording
    PICTURE      = 'picture'
    #
    VIDEO_FILE_EXT = ('mp4', 'mkv', 'webm', 'mpg', 'mpeg', 'mpe', 'mpv', 'mp4', 'm4v', 'avi', 'wmv', 'mov')
    # this is input csv format sequence
    INPUT_CSV_HEADER = (DLTYPE, DLINK, ALBUMARTIST, ALBUM, TITLE, ARTIST, GENRE, YEAR, PICTURE)
    #
    def __init__(self, **kwargs):
        self.inputList        = None
        self.outputFolder     = None
        self.downloaded_fname = None
        self.logger           = None
        self.verbose          = None
        #
        self.convert_to_mkv   = False if not kwargs.get('converttomkv', False) else True
        self.rm_cache_dir     = False if not kwargs.get('rmcachedir', False) else True
        #
        self.verbose          = self.set_verbosity(kwargs.get('verbose', logging.NOTSET))  # integer
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
        self.tempFolder       = self.TEMP_FOLDER
        self.tempVideoFolder  = os.path.join(self.tempFolder, 'video')
        self.tempAudioFolder  = os.path.join(self.tempFolder, 'audio')
        self.cacheFolder      = os.path.join(self.tempFolder, 'cache') # must contains 'cache' or 'tmp'
        # create folders
        for d in (self.tempFolder, self.tempVideoFolder, self.tempAudioFolder, self.cacheFolder):
            try:
                os.makedirs(d)
            except OSError:
                if not os.path.isdir(d):
                    raise DLFolderError('Create folder failed: {}'.format(d))
        #
        coverfolder = kwargs.get('coverfolder', self.COVER_FOLDER)
        if coverfolder is not None:
            self.coverFolder = os.path.abspath(coverfolder)
        else:
            self.coverFolder = self.COVER_FOLDER
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
        logFile = os.path.join(self.tempFolder, 'dl-youtube_{}.log'.format(now.strftime('%Y%m%d_%H%M%S')))
        fileHandler = logging.FileHandler(logFile)
        fileHandler.setFormatter(formatter)
        self.logger.addHandler(fileHandler)
        # check binary executables, etc
        self.logger.info('INFO:: Generated by ' + os.path.basename(__file__) + ' ' + __version__ + ' on ' + now.strftime('%d-%b-%Y %H:%M:%S'))
        self.logger.info('INFO:: Cache folder          : ' + self.cacheFolder)
        self.logger.info('INFO:: Temporary video folder: ' + self.tempVideoFolder)
        self.logger.info('INFO:: Temporary audio folder: ' + self.tempAudioFolder)
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
    def downloaded_video_file_exist(self, fnamenoext):
        for fname in [fnamenoext + '.' + fext for fext in self.VIDEO_FILE_EXT]:
            if os.path.isfile(fname):
                return fname
        else:
            return None
    #
    def get_dl_type(self, opt):
        getaudio = False
        getvideo = False
        if opt.lower() == 'a':
            getaudio = True
        elif opt.lower() == 'v':
            getvideo = True
        elif opt.lower() == 'av':
            getaudio = True
            getvideo = True
        else:
            pass
        return (getaudio, getvideo)

    #
    def ydl_hook(self, d):
        if d['status'] == 'finished':
            self.downloaded_fname = '{}'.format(d['filename'])
            msg = 'Downloaded {}'.format(self.downloaded_fname)
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
            # 'rm_cachedir'   : True,   # delete by self.rm_cache_dir() before download
            'verbose'       : True,
            'cachedir'      : self.cacheFolder,
            'keepvideo'     : True,
            'logger'        : self.logger,
            'noprogress'    : True,
            'progress_hooks': [self.ydl_hook]
        }
        #
        if self.rm_cache_dir:
            if os.path.isdir(self.cacheFolder):
                # self.cacheFolder will be recreated by youtub-dl later during download
                self.logger.info('INFO:: Removing cache folder: {}'.format(self.cacheFolder))
                shutil.rmtree(self.cacheFolder, ignore_errors=True)
            else:
                self.logger.debug('DEBUG:: Not removing cache folder: {}. It does not exist!!'.format(self.cacheFolder))

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

            # get download type
            getaudio, getvideo = self.get_dl_type(od[self.DLTYPE].strip().strip('"'))
            # non-alphanumeric change to underscore
            albumartist_fname = re.sub('[^0-9a-zA-Z]+', '_', albumartist)
            # albumartist_fpath: output/albumartist_fname/
            albumartist_fpath = os.path.join(self.outputFolder, albumartist_fname)
            album_fname  = re.sub('[^0-9a-zA-Z]+', '_', album)
            # album_fpath:       output/albumartist_fname/album_fname/
            album_fpath  = os.path.join(albumartist_fpath, album_fname)
            song_fname   = re.sub('[^0-9a-zA-Z]+', '_', song)       # title           (without file ext)
            song_fpath   = os.path.join(album_fpath, song_fname)    # title full path (without file ext)
            #
            audio_tmp_albumartist_fpath = os.path.join(self.tempAudioFolder, albumartist_fname)  # folder
            audio_tmp_album_fpath       = os.path.join(audio_tmp_albumartist_fpath, album_fname) # folder
            audio_tmp_fpath             = os.path.join(audio_tmp_album_fpath, song_fname)        # folder
            video_tmp_albumartist_fpath = os.path.join(self.tempVideoFolder, albumartist_fname)  # folder
            video_tmp_album_fpath       = os.path.join(video_tmp_albumartist_fpath, album_fname) # filename, not folder
            video_tmp_fpath             = os.path.join(video_tmp_album_fpath, song_fname)        # filename, not folder
            #
            for d in (video_tmp_albumartist_fpath, video_tmp_album_fpath,
                      audio_tmp_albumartist_fpath, audio_tmp_album_fpath,
                      albumartist_fpath, album_fpath):
                try:
                    os.makedirs(d)
                except OSError:
                    if not os.path.isdir(d):
                        continue
            #
            isyoutube    = self.isYoutubeLink(dlink)
            if self.convert_to_mkv is True or isyoutube is True:
                converttomkv = True
            else:
                converttomkv = False
            #
            try:
                # VIDEO (including it's audio, of course)
                self.downloaded_fname = None
                if getvideo:
                    self.logger.info('')
                    self.logger.info('INFO:: Processing Video "{}"'.format(song))
                    # best guest if we already downloaded video file from previous runs
                    videotmpfpath = self.downloaded_video_file_exist(video_tmp_fpath)
                    # best guest if we already have target normalized video file
                    videofpath    = self.downloaded_video_file_exist(song_fpath)
                    #
                    # 1. Download video. Input: youtube link, output: file in videotmpfpath folder
                    #
                    if videofpath is not None:
                        self.logger.debug('DEBUG:: Skip: Target video file "{}" already exist!'.format(videofpath))
                    else:
                        if videotmpfpath is not None:
                            self.logger.debug('DEBUG:: Skip downloading: Video file "{}" already exist!'.format(videotmpfpath))
                        else:
                            self.logger.info('INFO:: Downloading video: {} ...'.format(song))
                            vpostprocessors = []
                            ydl_video_opts = {
                                'outtmpl'        : video_tmp_fpath + '.%(ext)s'
                            }
                            #
                            if converttomkv:
                                vpostprocessors.append({
                                    'key'           : 'FFmpegVideoConvertor',
                                    'preferedformat': 'mkv'
                                })
                            #
                            if isyoutube:
                                ydl_video_opts.update({'format'               : 'bestvideo+bestaudio'})
                                ydl_video_opts.update({'writesubtitles'       : True})  # --write-sub
                                ydl_video_opts.update({'embedsubtitles'       : True})  # --embed-subs
                                # ydl_video_opts.update({'subtitlesformat'      : 'srt'})
                                ydl_video_opts.update({'subtitleslangs'       : ['en']})
                                vpostprocessors.append({'key'   : 'FFmpegEmbedSubtitle'})
                            #
                            if vpostprocessors:
                                ydl_video_opts.update({'postprocessors' : vpostprocessors})
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
                            # we now know the actual downloaded filename including the extension
                            if converttomkv:
                                # youtube downloaded filename: *.f251.mp4 and *.f251.webm
                                videotmpfpath = video_tmp_fpath + '.mkv'
                            else:
                                # non youtube downloaded filename: *.mp4
                                videotmpfpath = self.downloaded_fname  # output of YoutubeDL, as input for FFmpegNormalize
                        #
                        # _ , ext    = os.path.splitext(videotmpfpath)
                        ext = '.mkv' if converttomkv else os.path.splitext(videotmpfpath)[1]
                        videofpath = song_fpath + ext  # target output of FFmpegNormalize
                        # Delete existing target normalized video, if any
                        # try:
                        #     os.remove(videofpath)
                        # except OSError:
                        #     pass
                        #
                        # 2. Normalize video. Input video from videotmpfpath, output video to videofpath
                        #
                        if os.path.isfile(videofpath):
                            self.logger.debug('DEBUG:: Skip normalizing: Target video file "{}" already exist!'.format(videofpath))
                        else:
                            if not isyoutube: # no need normalize if it's not youtube
                                shutil.copyfile(videotmpfpath, videofpath)
                                self.logger.info('INFO:: Copying done. Output file: {}'.format(videofpath))
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
                self.downloaded_fname = None
                if getaudio and isyoutube:
                    self.logger.info('')
                    self.logger.info('INFO:: Processing Audio "{}"'.format(song))
                    # 3. Download Audio
                    audiotmpfpath = audio_tmp_fpath + '.mp3'
                    audiofpath    = song_fpath + '.mp3'
                    if os.path.isfile(audiofpath):
                        self.logger.debug('DEBUG:: Skip: Target audio file "{}" already exist!'.format(audiofpath))
                    else:
                        if os.path.isfile(audiotmpfpath):
                            self.logger.debug('DEBUG:: Skip downloading: Audio file "{}" already exist!'.format(audiotmpfpath))
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
    # Note: The store_true option automatically creates a default value of False.
    #       Likewise, store_false will default to True when the command-line argument is not present.
    #
    def main(args):
        opts = { 'verbose'         : args.verbose,
                 'inputlist'       : args.inputlist,
                 'converttomkv'    : args.converttomkv,
                 'coverfolder'     : args.coverfolder,
                 'rmcachedir'      : args.rmcachedir,
                 'folderoutput'    : args.outputfolder}
        DLYoutube(**opts).main()

    # setup parser
    parser = argparse.ArgumentParser(description='Youtube Downloader and Stuffs')
    parser.add_argument('--version', action='version', version=__progname_version__)
    parser.add_argument('-m', '--convert-to-mkv', dest='converttomkv', action='store_true', help='Video convert to mkv')
    parser.add_argument('-r', '--rm-cache-dir', dest='rmcachedir', action='store_true', help='Remove cache directory')
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
