# -*- coding: utf-8 -*-
#
# Picard, the next-generation MusicBrainz tagger
# Copyright (C) 2006-2007 Lukáš Lalinský
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.

import mutagen.apev2
import mutagen.mp3
import mutagen.trueaudio
from mutagen import id3
from picard.metadata import Metadata
from picard.file import File
from picard.formats.mutagenext import compatid3
from picard.util import encode_filename, sanitize_date


# Ugly, but... I need to save the text in ISO-8859-1 even if it contains
# unsupported characters and this better than encoding, decoding and
# again encoding.
def patched_EncodedTextSpec_write(self, frame, value):
    try:
        enc, term = self._encodings[frame.encoding]
    except AttributeError:
        enc, term = self.encodings[frame.encoding]
    return value.encode(enc, 'ignore') + term
id3.EncodedTextSpec.write = patched_EncodedTextSpec_write


# One more "monkey patch". The ID3 spec says that multiple text
# values should be _separated_ by the string terminator, which
# means that e.g. 'a\x00' are two values, 'a' and ''.
def patched_MultiSpec_write(self, frame, value):
    data = self._write_orig(frame, value)
    spec = self.specs[-1]
    if isinstance(spec, id3.EncodedTextSpec):
        try:
            term = spec._encodings[frame.encoding][1]
        except AttributeError:
            term = spec.encodings[frame.encoding][1]
        if data.endswith(term):
            data = data[:-len(term)]
    return data
id3.MultiSpec._write_orig = id3.MultiSpec.write
id3.MultiSpec.write = patched_MultiSpec_write


class ID3File(File):
    """Generic ID3-based file."""
    _File = None
    _IsMP3 = False

    __upgrade = {
        'XSOP': 'TSOP',
        'XDOR': 'TDRC',
    }

    __translate = {
        'TPE1': 'artist',
        'TPE2': 'albumartist',
        'TPE3': 'conductor',
        'TPE4': 'remixer',
        'TCOM': 'composer',
        'TCON': 'genre',
        'TALB': 'album',
        'TIT1': 'grouping',
        'TIT2': 'title',
        'TIT3': 'subtitle',
        'TSST': 'discsubtitle',
        'TEXT': 'lyricist',
        'TCMP': 'compilation',
        'TDRC': 'date',
        'TDOR': 'originaldate',
        'COMM': 'comment',
        'TMOO': 'mood',
        'TMED': 'media',
        'TBPM': 'bpm',
        'WOAR': 'website',
        'TSRC': 'isrc',
        'TENC': 'encodedby',
        'TCOP': 'copyright',
        'TSOA': 'albumsort',
        'TSOP': 'artistsort',
        'TSOT': 'titlesort',
        'TPUB': 'label',
    }
    __rtranslate = dict([(v, k) for k, v in __translate.iteritems()])

    __translate_freetext = {
        'MusicBrainz Artist Id': 'musicbrainz_artistid',
        'MusicBrainz Album Id': 'musicbrainz_albumid',
        'MusicBrainz Album Artist Id': 'musicbrainz_albumartistid',
        'MusicBrainz Album Type': 'releasetype',
        'MusicBrainz Album Status': 'releasestatus',
        'MusicBrainz TRM Id': 'musicbrainz_trmid',
        'MusicBrainz Disc Id': 'musicbrainz_discid',
        'MusicBrainz Album Release Country': 'releasecountry',
        'MusicIP PUID': 'musicip_puid',
        'ALBUMARTISTSORT': 'albumartistsort',
        'CATALOGNUMBER': 'catalognumber',
        'BARCODE': 'barcode',
        'ASIN': 'asin',
        'MusicMagic Fingerprint': 'musicip_fingerprint',
    }
    __rtranslate_freetext = dict([(v, k) for k, v in __translate_freetext.iteritems()])

    __tipl_roles = {
        'engineer': 'engineer',
        'arranger': 'arranger',
        'producer': 'producer',
        'DJ-mix': 'djmixer',
        'mix': 'mixer',
    }
    __rtipl_roles = dict([(v, k) for k, v in __tipl_roles.iteritems()])

    def _load(self, filename):
        self.log.debug("Loading file %r", filename)
        file = self._File(encode_filename(filename), ID3=compatid3.CompatID3)
        tags = file.tags or {}
        # upgrade custom 2.3 frames to 2.4
        for old, new in self.__upgrade.items():
            if old in tags and new not in tags:
                f = tags.pop(old)
                tags.add(getattr(id3, new)(encoding=f.encoding, text=f.text))
        metadata = Metadata()
        for frame in tags.values():
            frameid = frame.FrameID
            if frameid in self.__translate:
                name = self.__translate[frameid]
                if frameid.startswith('T'):
                    for text in frame.text:
                        if text:
                            metadata.add(name, unicode(text))
                elif frameid == 'COMM':
                    for text in frame.text:
                        if text:
                            metadata.add('%s:%s' % (name, frame.desc), unicode(text))
                else:
                    metadata.add(name, unicode(frame))
            elif frameid == "TMCL":
                for role, name in frame.people:
                    if role or name:
                        metadata.add('performer:%s' % role, name)
            elif frameid == "TIPL":
                for role, name in frame.people:
                    if role in self.__tipl_roles and name:
                        metadata.add(self.__tipl_roles[role], name)
            elif frameid == 'TXXX' and frame.desc in self.__translate_freetext:
                name = self.__translate_freetext[frame.desc]
                for text in frame.text:
                    metadata.add(name, unicode(text))
            elif frameid == 'USLT':
                metadata.add('lyrics:' + frame.desc, unicode(frame.text))
            elif frameid == 'UFID' and frame.owner == 'http://musicbrainz.org':
                metadata['musicbrainz_trackid'] = unicode(frame.data)
            elif frameid == 'TRCK':
                value = frame.text[0].split('/')
                if len(value) > 1:
                    metadata['tracknumber'], metadata['totaltracks'] = value[:2]
                else:
                    metadata['tracknumber'] = value[0]
            elif frameid == 'TPOS':
                value = frame.text[0].split('/')
                if len(value) > 1:
                    metadata['discnumber'], metadata['totaldiscs'] = value[:2]
                else:
                    metadata['discnumber'] = value[0]
            elif frameid == 'APIC':
                metadata.add_image(frame.mime, frame.data)
            elif frameid == 'POPM':
                # Rating in ID3 ranges from 0 to 255, normalize this to the range 0 to 5
                if frame.email == self.config.setting['rating_user_email']:
                    rating = unicode(int(round(frame.rating / 255.0 * (self.config.setting['rating_steps'] - 1))))
                    metadata.add('~rating', rating)

        if 'date' in metadata:
            metadata['date'] = sanitize_date(metadata.getall('date')[0])

        self._info(metadata, file)
        return metadata

    def _save(self, filename, metadata, settings):
        """Save metadata to the file."""
        self.log.debug("Saving file %r", filename)
        try:
            tags = compatid3.CompatID3(encode_filename(filename))
        except mutagen.id3.ID3NoHeaderError:
            tags = compatid3.CompatID3()

        if settings['clear_existing_tags']:
            tags.clear()
        if settings['remove_images_from_tags']:
            tags.delall('APIC')

        if settings['write_id3v1']:
            v1 = 2
        else:
            v1 = 0
        encoding = {'utf-8': 3, 'utf-16': 1}.get(settings['id3v2_encoding'], 0)

        if 'tracknumber' in metadata:
            if 'totaltracks' in metadata:
                text = '%s/%s' % (metadata['tracknumber'], metadata['totaltracks'])
            else:
                text = metadata['tracknumber']
            tags.add(id3.TRCK(encoding=0, text=text))

        if 'discnumber' in metadata:
            if 'totaldiscs' in metadata:
                text = '%s/%s' % (metadata['discnumber'], metadata['totaldiscs'])
            else:
                text = metadata['discnumber']
            tags.add(id3.TPOS(encoding=0, text=text))

        if settings['save_images_to_tags']:
            for mime, data in metadata.images:
                tags.add(id3.APIC(encoding=0, mime=mime, type=3, desc='', data=data))

        tmcl = mutagen.id3.TMCL(encoding=encoding, people=[])
        tipl = mutagen.id3.TIPL(encoding=encoding, people=[])

        id3.TCMP = compatid3.TCMP
        tags.delall('TCMP')
        for name, values in metadata.rawitems():
            if name.startswith('performer:'):
                role = name.split(':', 1)[1]
                for value in values:
                    tmcl.people.append([role, value])
            elif name.startswith('comment:'):
                desc = name.split(':', 1)[1]
                tags.add(id3.COMM(encoding=encoding, desc=desc, text=values))
            elif name.startswith('lyrics:') or name == 'lyrics':
                if ':' in name:
                    desc = name.split(':', 1)[1]
                else:
                    desc = ''
                for value in values:
                    tags.add(id3.USLT(encoding=encoding, desc=desc, text=value))
            elif name in self.__rtipl_roles:
                for value in values:
                    tipl.people.append([self.__rtipl_roles[name], value])
            elif name == 'musicbrainz_trackid':
                tags.add(id3.UFID(owner='http://musicbrainz.org', data=str(values[0])))
            elif name == '~rating':
                # Search for an existing POPM frame to get the current playcount
                for frame in tags.values():
                    if frame.FrameID == 'POPM' and frame.email == settings['rating_user_email']:
                        count = frame.count
                        break
                else:
                    count = 0
                
                # Convert rating to range between 0 and 255
                rating = int(values[0]) * 255 / (settings['rating_steps'] - 1)
                tags.add(id3.POPM(email=settings['rating_user_email'], rating=rating, count=count))
            elif name in self.__rtranslate:
                frameid = self.__rtranslate[name]
                if frameid.startswith('W'):
                    tags.add(getattr(id3, frameid)(url=values[0]))
                elif frameid.startswith('T'):
                    tags.add(getattr(id3, frameid)(encoding=encoding, text=values))
            elif name in self.__rtranslate_freetext:
                tags.add(id3.TXXX(encoding=encoding, desc=self.__rtranslate_freetext[name], text=values))
            elif name.startswith('~id3:'):
                name = name[5:]
                if name.startswith('TXXX:'):
                    tags.add(id3.TXXX(encoding=encoding, desc=name[5:], text=values))
                else:
                    frameclass = getattr(id3, name[:4], None)
                    if frameclass:
                        tags.add(frameclass(encoding=encoding, text=values))

        if tmcl.people:
            tags.add(tmcl)
        if tipl.people:
            tags.add(tipl)

        if settings['write_id3v23']:
            tags.update_to_v23()
            tags.save(encode_filename(filename), v2=3, v1=v1)
        else:
            # remove all custom 2.3 frames
            for old in self.__upgrade.keys():
                tags.delall(old)
            tags.update_to_v24()
            tags.save(encode_filename(filename), v2=4, v1=v1)

        if self._IsMP3 and settings["remove_ape_from_mp3"]:
            try: mutagen.apev2.delete(encode_filename(filename))
            except: pass

    def supports_tag(self, name):
        return name in self.__rtranslate or name in self.__rtranslate_freetext or name.startswith('performer:')


class MP3File(ID3File):
    """MP3 file."""
    EXTENSIONS = [".mp3", ".mp2"]
    NAME = "MPEG-1 Audio"
    _File = mutagen.mp3.MP3
    _IsMP3 = True
    def _info(self, metadata, file):
        super(MP3File, self)._info(metadata, file)
        metadata['~format'] = 'MPEG-1 Layer %d' % file.info.layer

class TrueAudioFile(ID3File):
    """TTA file."""
    EXTENSIONS = [".tta"]
    NAME = "The True Audio"
    _File = mutagen.trueaudio.TrueAudio
    def _info(self, metadata, file):
        super(TrueAudioFile, self)._info(metadata, file)
        metadata['~format'] = self.NAME
