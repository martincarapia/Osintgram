import datetime
import json
import sys
import urllib
import os
import logging
from pathlib import Path

import httpx

from geopy.geocoders import Nominatim
from instagrapi import Client
from instagrapi.exceptions import (
    BadPassword,
    ChallengeRequired,
    FeedbackRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
    ReloginAttemptExceeded,
    ClientThrottledError,
    RecaptchaChallengeForm,
    SelectContactPointRecoveryForm,
)
from instagrapi.utils import json_value

from prettytable import PrettyTable

from src import printcolors as pc
from src import config

logger = logging.getLogger(__name__)


class Osintgram:
    api = None
    api2 = None
    geolocator = Nominatim(user_agent="http")
    user_id = None
    target_id = None
    is_private = True
    following = False
    target = ""
    writeFile = False
    jsonDump = False
    cli_mode = False
    output_dir = "output"


    def __init__(self, target, is_file, is_json, is_cli, output_dir, clear_cookies):
        self.output_dir = output_dir or self.output_dir        
        u = config.getUsername()
        p = config.getPassword()
        s = config.getSessionId()
        self.clear_cookies(clear_cookies)
        self.cli_mode = is_cli
        if not is_cli:
          print("\nAttempt to login...")
        self.login(u, p, session_id=s)
        self.setTarget(target)
        self.writeFile = is_file
        self.jsonDump = is_json

    def clear_cookies(self,clear_cookies):
        if clear_cookies:
            self.clear_cache()

    def setTarget(self, target):
        self.target = target
        user = self.get_user(target)
        self.target_id = user['id']
        self.is_private = user['is_private']
        self.following = self.check_following()
        self.__printTargetBanner__()
        self.output_dir = self.output_dir + "/" + str(self.target)
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)

    def __get_feed__(self):
        medias = self.api.user_medias(self.target_id, amount=0)
        data = []
        for m in medias:
            data.append(self._media_to_dict(m))
        return data

    def _media_to_dict(self, media):
        d = {
            'id': media.id,
            'pk': media.pk,
            'taken_at': int(media.taken_at.timestamp()) if media.taken_at else None,
            'media_type': media.media_type,
            'like_count': media.like_count or 0,
            'comment_count': media.comment_count or 0,
            'caption': None,
            'location': None,
            'user': {
                'pk': media.user.pk if media.user else None,
                'username': media.user.username if media.user else None,
                'full_name': media.user.full_name if media.user else None,
            },
        }

        if media.caption_text:
            d['caption'] = {'text': media.caption_text}

        if media.location:
            d['location'] = {
                'lat': media.location.lat,
                'lng': media.location.lng,
                'name': media.location.name if hasattr(media.location, 'name') else None,
            }

        if media.media_type == 1 and media.thumbnail_url:
            d['image_versions2'] = {
                'candidates': [{'url': str(media.thumbnail_url)}]
            }
        elif media.media_type == 2 and media.video_url:
            d['video_url'] = str(media.video_url)
            if media.thumbnail_url:
                d['image_versions2'] = {
                    'candidates': [{'url': str(media.thumbnail_url)}]
                }
        elif media.media_type == 8 and media.resources:
            carousel = []
            for r in media.resources:
                item = {'id': str(r.pk), 'media_type': r.media_type}
                if r.thumbnail_url:
                    item['image_versions2'] = {
                        'candidates': [{'url': str(r.thumbnail_url)}]
                    }
                if r.video_url:
                    item['video_url'] = str(r.video_url)
                carousel.append(item)
            d['carousel_media'] = carousel

        if media.usertags:
            d['usertags'] = {
                'in': [
                    {
                        'user': {
                            'pk': ut.user.pk,
                            'username': ut.user.username,
                            'full_name': ut.user.full_name,
                        }
                    }
                    for ut in media.usertags
                ]
            }

        return d

    def __get_comments__(self, media_id):
        comments_raw = self.api.media_comments(media_id, amount=0)
        comments = []
        for c in comments_raw:
            comments.append({
                'text': c.text,
                'user': {
                    'pk': c.user.pk,
                    'username': c.user.username,
                    'full_name': c.user.full_name,
                },
                'user_id': c.user.pk,
            })
        return comments

    def __printTargetBanner__(self):
        pc.printout("\nLogged as ", pc.GREEN)
        pc.printout(self.api.username, pc.CYAN)
        pc.printout(". Target: ", pc.GREEN)
        pc.printout(str(self.target), pc.CYAN)
        pc.printout(" [" + str(self.target_id) + "]")
        if self.is_private:
            pc.printout(" [PRIVATE PROFILE]", pc.BLUE)
        if self.following:
            pc.printout(" [FOLLOWING]", pc.GREEN)
        else:
            pc.printout(" [NOT FOLLOWING]", pc.RED)

        print('\n')

    def change_target(self):
        pc.printout("Insert new target username: ", pc.YELLOW)
        line = input()
        self.setTarget(line)
        return

    def get_addrs(self):
        if self.check_private_profile():
            return

        pc.printout("Searching for target localizations...\n")

        data = self.__get_feed__()

        locations = {}

        for post in data:
            if 'location' in post and post['location'] is not None:
                if 'lat' in post['location'] and 'lng' in post['location']:
                    lat = post['location']['lat']
                    lng = post['location']['lng']
                    locations[str(lat) + ', ' + str(lng)] = post.get('taken_at')

        address = {}
        for k, v in locations.items():
            details = self.geolocator.reverse(k)
            unix_timestamp = datetime.datetime.fromtimestamp(v)
            address[details.address] = unix_timestamp.strftime('%Y-%m-%d %H:%M:%S')

        sort_addresses = sorted(address.items(), key=lambda p: p[1], reverse=True)

        if len(sort_addresses) > 0:
            t = PrettyTable()

            t.field_names = ['Post', 'Address', 'time']
            t.align["Post"] = "l"
            t.align["Address"] = "l"
            t.align["Time"] = "l"
            pc.printout("\nWoohoo! We found " + str(len(sort_addresses)) + " addresses\n", pc.GREEN)

            i = 1

            json_data = {}
            addrs_list = []

            for address, time in sort_addresses:
                t.add_row([str(i), address, time])

                if self.jsonDump:
                    addr = {
                        'address': address,
                        'time': time
                    }
                    addrs_list.append(addr)

                i = i + 1

            if self.writeFile:
                file_name = self.output_dir + "/" + self.target + "_addrs.txt"
                file = open(file_name, "w")
                file.write(str(t))
                file.close()

            if self.jsonDump:
                json_data['address'] = addrs_list
                json_file_name = self.output_dir + "/" + self.target + "_addrs.json"
                with open(json_file_name, 'w') as f:
                    json.dump(json_data, f)

            print(t)
        else:
            pc.printout("Sorry! No results found :-(\n", pc.RED)

    def get_captions(self):
        if self.check_private_profile():
            return

        pc.printout("Searching for target captions...\n")

        captions = []

        data = self.__get_feed__()
        counter = 0

        try:
            for item in data:
                if "caption" in item:
                    if item["caption"] is not None:
                        text = item["caption"]["text"]
                        captions.append(text)
                        counter = counter + 1
                        sys.stdout.write("\rFound %i" % counter)
                        sys.stdout.flush()

        except AttributeError:
            pass

        except KeyError:
            pass

        json_data = {}

        if counter > 0:
            pc.printout("\nWoohoo! We found " + str(counter) + " captions\n", pc.GREEN)

            file = None

            if self.writeFile:
                file_name = self.output_dir + "/" + self.target + "_captions.txt"
                file = open(file_name, "w")

            for s in captions:
                print(s + "\n")

                if self.writeFile:
                    file.write(s + "\n")

            if self.jsonDump:
                json_data['captions'] = captions
                json_file_name = self.output_dir + "/" + self.target + "_followings.json"
                with open(json_file_name, 'w') as f:
                    json.dump(json_data, f)

            if file is not None:
                file.close()

        else:
            pc.printout("Sorry! No results found :-(\n", pc.RED)

        return

    def get_total_comments(self):
        if self.check_private_profile():
            return

        pc.printout("Searching for target total comments...\n")

        comments_counter = 0
        posts = 0

        data = self.__get_feed__()

        for post in data:
            comments_counter += post['comment_count']
            posts += 1

        if self.writeFile:
            file_name = self.output_dir + "/" + self.target + "_comments.txt"
            file = open(file_name, "w")
            file.write(str(comments_counter) + " comments in " + str(posts) + " posts\n")
            file.close()

        if self.jsonDump:
            json_data = {
                'comment_counter': comments_counter,
                'posts': posts
            }
            json_file_name = self.output_dir + "/" + self.target + "_comments.json"
            with open(json_file_name, 'w') as f:
                json.dump(json_data, f)

        pc.printout(str(comments_counter), pc.MAGENTA)
        pc.printout(" comments in " + str(posts) + " posts\n")

    def get_comment_data(self):
        if self.check_private_profile():
            return

        pc.printout("Retrieving all comments, this may take a moment...\n")
        data = self.__get_feed__()
        
        _comments = []
        t = PrettyTable(['POST ID', 'ID', 'Username', 'Comment'])
        t.align["POST ID"] = "l"
        t.align["ID"] = "l"
        t.align["Username"] = "l"
        t.align["Comment"] = "l"

        for post in data:
            post_id = post.get('id')
            comments = self.__get_comments__(post_id)
            for comment in comments:
                t.add_row([post_id, comment.get('user_id'), comment.get('user').get('username'), comment.get('text')])
                comment_data = {
                    "post_id": post_id,
                    "user_id": comment.get('user_id'),
                    "username": comment.get('user').get('username'),
                    "comment": comment.get('text')
                }
                _comments.append(comment_data)
        
        print(t)
        if self.writeFile:
            file_name = self.output_dir + "/" + self.target + "_comment_data.txt"
            with open(file_name, 'w') as f:
                f.write(str(t))
                f.close()
        
        if self.jsonDump:
            file_name_json = self.output_dir + "/" + self.target + "_comment_data.json"
            with open(file_name_json, 'w') as f:
                f.write("{ \"Comments\":[ \n")
                f.write('\n'.join(json.dumps(c) for c in _comments) + ',\n')
                f.write("]} ")


    def get_followers(self):
        if self.check_private_profile():
            return

        pc.printout("Searching for target followers...\n")

        followers_dict = self.api.user_followers(self.target_id)
        followers = []

        for user_id, user_short in followers_dict.items():
            u = {
                'id': user_short.pk,
                'username': user_short.username,
                'full_name': user_short.full_name or ''
            }
            followers.append(u)

        pc.printout("\nFound " + str(len(followers)) + " followers\n", pc.GREEN)

        t = PrettyTable(['ID', 'Username', 'Full Name'])
        t.align["ID"] = "l"
        t.align["Username"] = "l"
        t.align["Full Name"] = "l"

        json_data = {}
        followings_list = []

        for node in followers:
            t.add_row([str(node['id']), node['username'], node['full_name']])

            if self.jsonDump:
                follow = {
                    'id': node['id'],
                    'username': node['username'],
                    'full_name': node['full_name']
                }
                followings_list.append(follow)

        if self.writeFile:
            file_name = self.output_dir + "/" + self.target + "_followers.txt"
            file = open(file_name, "w")
            file.write(str(t))
            file.close()

        if self.jsonDump:
            json_data['followers'] = followers
            json_file_name = self.output_dir + "/" + self.target + "_followers.json"
            with open(json_file_name, 'w') as f:
                json.dump(json_data, f)

        print(t)

    def get_followings(self):
        if self.check_private_profile():
            return

        pc.printout("Searching for target followings...\n")

        following_dict = self.api.user_following(self.target_id)
        followings = []

        for user_id, user_short in following_dict.items():
            u = {
                'id': user_short.pk,
                'username': user_short.username,
                'full_name': user_short.full_name or ''
            }
            followings.append(u)

        pc.printout("\nFound " + str(len(followings)) + " followings\n", pc.GREEN)

        t = PrettyTable(['ID', 'Username', 'Full Name'])
        t.align["ID"] = "l"
        t.align["Username"] = "l"
        t.align["Full Name"] = "l"

        json_data = {}
        followings_list = []

        for node in followings:
            t.add_row([str(node['id']), node['username'], node['full_name']])

            if self.jsonDump:
                follow = {
                    'id': node['id'],
                    'username': node['username'],
                    'full_name': node['full_name']
                }
                followings_list.append(follow)

        if self.writeFile:
            file_name = self.output_dir + "/" + self.target + "_followings.txt"
            file = open(file_name, "w")
            file.write(str(t))
            file.close()

        if self.jsonDump:
            json_data['followings'] = followings_list
            json_file_name = self.output_dir + "/" + self.target + "_followings.json"
            with open(json_file_name, 'w') as f:
                json.dump(json_data, f)

        print(t)

    def get_hashtags(self):
        if self.check_private_profile():
            return

        pc.printout("Searching for target hashtags...\n")

        hashtags = []
        counter = 1

        data = self.__get_feed__()

        for post in data:
            if post['caption'] is not None:
                caption = post['caption']['text']
                for s in caption.split():
                    if s.startswith('#'):
                        hashtags.append(s.encode('UTF-8'))
                        counter += 1

        if len(hashtags) > 0:
            hashtag_counter = {}

            for i in hashtags:
                if i in hashtag_counter:
                    hashtag_counter[i] += 1
                else:
                    hashtag_counter[i] = 1

            ssort = sorted(hashtag_counter.items(), key=lambda value: value[1], reverse=True)

            file = None
            json_data = {}
            hashtags_list = []

            if self.writeFile:
                file_name = self.output_dir + "/" + self.target + "_hashtags.txt"
                file = open(file_name, "w")

            for k, v in ssort:
                hashtag = str(k.decode('utf-8'))
                print(str(v) + ". " + hashtag)
                if self.writeFile:
                    file.write(str(v) + ". " + hashtag + "\n")
                if self.jsonDump:
                    hashtags_list.append(hashtag)

            if file is not None:
                file.close()

            if self.jsonDump:
                json_data['hashtags'] = hashtags_list
                json_file_name = self.output_dir + "/" + self.target + "_hashtags.json"
                with open(json_file_name, 'w') as f:
                    json.dump(json_data, f)
        else:
            pc.printout("Sorry! No results found :-(\n", pc.RED)

    def get_user_info(self):
        try:
            data = self.api.user_info(self.target_id)

            pc.printout("[ID] ", pc.GREEN)
            pc.printout(str(data.pk) + '\n')
            pc.printout("[FULL NAME] ", pc.RED)
            pc.printout(str(data.full_name) + '\n')
            pc.printout("[BIOGRAPHY] ", pc.CYAN)
            pc.printout(str(data.biography) + '\n')
            pc.printout("[FOLLOWED] ", pc.BLUE)
            pc.printout(str(data.follower_count) + '\n')
            pc.printout("[FOLLOW] ", pc.GREEN)
            pc.printout(str(data.following_count) + '\n')
            pc.printout("[BUSINESS ACCOUNT] ", pc.RED)
            pc.printout(str(data.is_business) + '\n')
            if data.is_business and data.category:
                pc.printout("[BUSINESS CATEGORY] ")
                pc.printout(str(data.category) + '\n')
            pc.printout("[VERIFIED ACCOUNT] ", pc.CYAN)
            pc.printout(str(data.is_verified) + '\n')
            if data.public_email:
                pc.printout("[EMAIL] ", pc.BLUE)
                pc.printout(str(data.public_email) + '\n')
            pc.printout("[HD PROFILE PIC] ", pc.GREEN)
            pc.printout(str(data.profile_pic_url_hd or data.profile_pic_url) + '\n')
            if data.external_url:
                pc.printout("[EXTERNAL URL] ", pc.CYAN)
                pc.printout(str(data.external_url) + '\n')
            if data.public_phone_number:
                pc.printout("[CONTACT PHONE NUMBER] ", pc.CYAN)
                pc.printout(str(data.public_phone_number) + '\n')
            if data.city_name:
                pc.printout("[CITY] ", pc.YELLOW)
                pc.printout(str(data.city_name) + '\n')
            if data.address_street:
                pc.printout("[ADDRESS STREET] ", pc.RED)
                pc.printout(str(data.address_street) + '\n')

            if self.jsonDump:
                user = {
                    'id': data.pk,
                    'full_name': data.full_name,
                    'biography': data.biography,
                    'edge_followed_by': data.follower_count,
                    'edge_follow': data.following_count,
                    'is_business_account': data.is_business,
                    'is_verified': data.is_verified,
                    'profile_pic_url_hd': str(data.profile_pic_url_hd or data.profile_pic_url)
                }
                if data.public_email:
                    user['email'] = data.public_email
                if data.external_url:
                    user['external_url'] = str(data.external_url)
                if data.public_phone_number:
                    user['contact_phone_number'] = str(data.public_phone_number)
                if data.city_name:
                    user['city_name'] = data.city_name
                if data.address_street:
                    user['address_street'] = data.address_street

                json_file_name = self.output_dir + "/" + self.target + "_info.json"
                with open(json_file_name, 'w') as f:
                    json.dump(user, f)

        except Exception as e:
            print(e)
            pc.printout("Oops... " + str(self.target) + " non exist, please enter a valid username.", pc.RED)
            pc.printout("\n")
            exit(2)

    def get_total_likes(self):
        if self.check_private_profile():
            return

        pc.printout("Searching for target total likes...\n")

        like_counter = 0
        posts = 0

        data = self.__get_feed__()

        likes = [int(p["like_count"]) for p in data]
        posts = len(likes)
        like_counter = sum(likes)
        min_ = min(likes)
        max_ = max(likes)
        avg = int(like_counter / posts)
        result = f" likes in {posts} posts (min: {min_}, max: {max_}, avg: {avg})\n"

        if self.writeFile:
            file_name = self.output_dir + "/" + self.target + "_likes.txt"
            file = open(file_name, "w")
            file.write(str(like_counter) + result)
            file.close()

        if self.jsonDump:
            json_data = {
                "like_counter": like_counter,
                "posts": posts,
                "min": min_,
                "max": max_,
                "avg": avg,
            }
            json_file_name = self.output_dir + "/" + self.target + "_likes.json"
            with open(json_file_name, "w") as f:
                json.dump(json_data, f)

        pc.printout(str(like_counter), pc.MAGENTA)
        pc.printout(result)

    def get_media_type(self):
        if self.check_private_profile():
            return

        pc.printout("Searching for target captions...\n")

        counter = 0
        photo_counter = 0
        video_counter = 0

        data = self.__get_feed__()

        for post in data:
            if "media_type" in post:
                if post["media_type"] == 1:
                    photo_counter = photo_counter + 1
                elif post["media_type"] == 2:
                    video_counter = video_counter + 1
                counter = counter + 1
                sys.stdout.write("\rChecked %i" % counter)
                sys.stdout.flush()

        sys.stdout.write(" posts")
        sys.stdout.flush()

        if counter > 0:

            if self.writeFile:
                file_name = self.output_dir + "/" + self.target + "_mediatype.txt"
                file = open(file_name, "w")
                file.write(str(photo_counter) + " photos and " + str(video_counter) + " video posted by target\n")
                file.close()

            pc.printout("\nWoohoo! We found " + str(photo_counter) + " photos and " + str(video_counter) +
                        " video posted by target\n", pc.GREEN)

            if self.jsonDump:
                json_data = {
                    "photos": photo_counter,
                    "videos": video_counter
                }
                json_file_name = self.output_dir + "/" + self.target + "_mediatype.json"
                with open(json_file_name, 'w') as f:
                    json.dump(json_data, f)

        else:
            pc.printout("Sorry! No results found :-(\n", pc.RED)

    def get_people_who_commented(self):
        if self.check_private_profile():
            return

        pc.printout("Searching for users who commented...\n")

        data = self.__get_feed__()
        users = []

        for post in data:
            comments = self.__get_comments__(post['id'])
            for comment in comments:
                if not any(u['id'] == comment['user']['pk'] for u in users):
                    user = {
                        'id': comment['user']['pk'],
                        'username': comment['user']['username'],
                        'full_name': comment['user']['full_name'],
                        'counter': 1
                    }
                    users.append(user)
                else:
                    for user in users:
                        if user['id'] == comment['user']['pk']:
                            user['counter'] += 1
                            break

        if len(users) > 0:
            ssort = sorted(users, key=lambda value: value['counter'], reverse=True)

            json_data = {}

            t = PrettyTable()

            t.field_names = ['Comments', 'ID', 'Username', 'Full Name']
            t.align["Comments"] = "l"
            t.align["ID"] = "l"
            t.align["Username"] = "l"
            t.align["Full Name"] = "l"

            for u in ssort:
                t.add_row([str(u['counter']), u['id'], u['username'], u['full_name']])

            print(t)

            if self.writeFile:
                file_name = self.output_dir + "/" + self.target + "_users_who_commented.txt"
                file = open(file_name, "w")
                file.write(str(t))
                file.close()

            if self.jsonDump:
                json_data['users_who_commented'] = ssort
                json_file_name = self.output_dir + "/" + self.target + "_users_who_commented.json"
                with open(json_file_name, 'w') as f:
                    json.dump(json_data, f)
        else:
            pc.printout("Sorry! No results found :-(\n", pc.RED)

    def get_people_who_tagged(self):
        if self.check_private_profile():
            return

        pc.printout("Searching for users who tagged target...\n")

        tagged_medias = self.api.usertag_medias(self.target_id, amount=0)
        posts = []
        for m in tagged_medias:
            posts.append(self._media_to_dict(m))

        if len(posts) > 0:
            pc.printout("\nWoohoo! We found " + str(len(posts)) + " photos\n", pc.GREEN)

            users = []

            for post in posts:
                if not any(u['id'] == post['user']['pk'] for u in users):
                    user = {
                        'id': post['user']['pk'],
                        'username': post['user']['username'],
                        'full_name': post['user']['full_name'],
                        'counter': 1
                    }
                    users.append(user)
                else:
                    for user in users:
                        if user['id'] == post['user']['pk']:
                            user['counter'] += 1
                            break

            ssort = sorted(users, key=lambda value: value['counter'], reverse=True)

            json_data = {}

            t = PrettyTable()

            t.field_names = ['Photos', 'ID', 'Username', 'Full Name']
            t.align["Photos"] = "l"
            t.align["ID"] = "l"
            t.align["Username"] = "l"
            t.align["Full Name"] = "l"

            for u in ssort:
                t.add_row([str(u['counter']), u['id'], u['username'], u['full_name']])

            print(t)

            if self.writeFile:
                file_name = self.output_dir + "/" + self.target + "_users_who_tagged.txt"
                file = open(file_name, "w")
                file.write(str(t))
                file.close()

            if self.jsonDump:
                json_data['users_who_tagged'] = ssort
                json_file_name = self.output_dir + "/" + self.target + "_users_who_tagged.json"
                with open(json_file_name, 'w') as f:
                    json.dump(json_data, f)
        else:
            pc.printout("Sorry! No results found :-(\n", pc.RED)

    def get_photo_description(self):
        if self.check_private_profile():
            return

        medias = self.api.user_medias(self.target_id, amount=0)

        if len(medias) > 0:
            pc.printout("\nWoohoo! We found " + str(len(medias)) + " descriptions\n", pc.GREEN)

            count = 1

            t = PrettyTable(['Photo', 'Description'])
            t.align["Photo"] = "l"
            t.align["Description"] = "l"

            json_data = {}
            descriptions_list = []

            for m in medias:
                descr = m.accessibility_caption or m.caption_text or ''
                t.add_row([str(count), descr])

                if self.jsonDump:
                    description = {
                        'description': descr
                    }
                    descriptions_list.append(description)

                count += 1

            if self.writeFile:
                file_name = self.output_dir + "/" + self.target + "_photodes.txt"
                file = open(file_name, "w")
                file.write(str(t))
                file.close()

            if self.jsonDump:
                json_data['descriptions'] = descriptions_list
                json_file_name = self.output_dir + "/" + self.target + "_descriptions.json"
                with open(json_file_name, 'w') as f:
                    json.dump(json_data, f)

            print(t)
        else:
            pc.printout("Sorry! No results found :-(\n", pc.RED)

    def get_user_photo(self):
        if self.check_private_profile():
            return

        limit = -1
        if self.cli_mode:
            user_input = ""
        else:
            pc.printout("How many photos you want to download (default all): ", pc.YELLOW)
            user_input = input()
          
        try:
            if user_input == "":
                pc.printout("Downloading all photos available...\n")
            else:
                limit = int(user_input)
                pc.printout("Downloading " + user_input + " photos...\n")

        except ValueError:
            pc.printout("Wrong value entered\n", pc.RED)
            return

        data = self.__get_feed__()
        counter = 0

        try:
            for item in data:
                if counter == limit:
                    break
                if "image_versions2" in item:
                    counter = counter + 1
                    url = item["image_versions2"]["candidates"][0]["url"]
                    photo_id = item["id"]
                    end = self.output_dir + "/" + self.target + "_" + str(photo_id) + ".jpg"
                    urllib.request.urlretrieve(url, end)
                    sys.stdout.write("\rDownloaded %i" % counter)
                    sys.stdout.flush()
                elif "carousel_media" in item:
                    carousel = item["carousel_media"]
                    for i in carousel:
                        if counter == limit:
                            break
                        if "image_versions2" in i:
                            counter = counter + 1
                            url = i["image_versions2"]["candidates"][0]["url"]
                            photo_id = i["id"]
                            end = self.output_dir + "/" + self.target + "_" + str(photo_id) + ".jpg"
                            urllib.request.urlretrieve(url, end)
                            sys.stdout.write("\rDownloaded %i" % counter)
                            sys.stdout.flush()

        except AttributeError:
            pass

        except KeyError:
            pass

        sys.stdout.write(" photos")
        sys.stdout.flush()

        pc.printout("\nWoohoo! We downloaded " + str(counter) + " photos (saved in " + self.output_dir + " folder) \n", pc.GREEN)

    def get_user_propic(self):

        try:
            data = self.api.user_info(self.target_id)
            URL = str(data.profile_pic_url_hd or data.profile_pic_url)

            if URL:
                end = self.output_dir + "/" + self.target + "_propic.jpg"
                urllib.request.urlretrieve(URL, end)
                pc.printout("Target propic saved in output folder\n", pc.GREEN)
            else:
                pc.printout("Sorry! No results found :-(\n", pc.RED)

        except Exception as e:
            print(e)
            exit(2)

    def get_user_stories(self):
        if self.check_private_profile():
            return

        pc.printout("Searching for target stories...\n")

        stories = self.api.user_stories(self.target_id)

        counter = 0

        if stories:
            counter = len(stories)
            for story in stories:
                story_id = story.id
                if story.media_type == 1:
                    url = str(story.thumbnail_url)
                    end = self.output_dir + "/" + self.target + "_" + str(story_id) + ".jpg"
                    urllib.request.urlretrieve(url, end)
                elif story.media_type == 2:
                    url = str(story.video_url)
                    end = self.output_dir + "/" + self.target + "_" + str(story_id) + ".mp4"
                    urllib.request.urlretrieve(url, end)

        if counter > 0:
            pc.printout(str(counter) + " target stories saved in output folder\n", pc.GREEN)
        else:
            pc.printout("Sorry! No results found :-(\n", pc.RED)

    def get_people_tagged_by_user(self):
        pc.printout("Searching for users tagged by target...\n")

        ids = []
        username = []
        full_name = []
        post = []
        counter = 1

        data = self.__get_feed__()

        try:
            for i in data:
                if "usertags" in i:
                    c = i.get('usertags').get('in')
                    for cc in c:
                        if cc.get('user').get('pk') not in ids:
                            ids.append(cc.get('user').get('pk'))
                            username.append(cc.get('user').get('username'))
                            full_name.append(cc.get('user').get('full_name'))
                            post.append(1)
                        else:
                            index = ids.index(cc.get('user').get('pk'))
                            post[index] += 1
                        counter = counter + 1
        except AttributeError as ae:
            pc.printout("\nERROR: an error occurred: ", pc.RED)
            print(ae)
            print("")
            pass

        if len(ids) > 0:
            t = PrettyTable()

            t.field_names = ['Posts', 'Full Name', 'Username', 'ID']
            t.align["Posts"] = "l"
            t.align["Full Name"] = "l"
            t.align["Username"] = "l"
            t.align["ID"] = "l"

            pc.printout("\nWoohoo! We found " + str(len(ids)) + " (" + str(counter) + ") users\n", pc.GREEN)

            json_data = {}
            tagged_list = []

            for i in range(len(ids)):
                t.add_row([post[i], full_name[i], username[i], str(ids[i])])

                if self.jsonDump:
                    tag = {
                        'post': post[i],
                        'full_name': full_name[i],
                        'username': username[i],
                        'id': ids[i]
                    }
                    tagged_list.append(tag)

            if self.writeFile:
                file_name = self.output_dir + "/" + self.target + "_tagged.txt"
                file = open(file_name, "w")
                file.write(str(t))
                file.close()

            if self.jsonDump:
                json_data['tagged'] = tagged_list
                json_file_name = self.output_dir + "/" + self.target + "_tagged.json"
                with open(json_file_name, 'w') as f:
                    json.dump(json_data, f)

            print(t)
        else:
            pc.printout("Sorry! No results found :-(\n", pc.RED)

    def get_user(self, username):
        try:
            content = self.api.user_info_by_username(username)
            if self.writeFile:
                file_name = self.output_dir + "/" + self.target + "_user_id.txt"
                file = open(file_name, "w")
                file.write(str(content.pk))
                file.close()

            user = dict()
            user['id'] = content.pk
            user['is_private'] = content.is_private

            return user
        except Exception as e:
            pc.printout(str(e), pc.RED)
            pc.printout("\n")
            sys.exit(2)


    def set_write_file(self, flag):
        if flag:
            pc.printout("Write to file: ")
            pc.printout("enabled", pc.GREEN)
            pc.printout("\n")
        else:
            pc.printout("Write to file: ")
            pc.printout("disabled", pc.RED)
            pc.printout("\n")

        self.writeFile = flag

    def set_json_dump(self, flag):
        if flag:
            pc.printout("Export to JSON: ")
            pc.printout("enabled", pc.GREEN)
            pc.printout("\n")
        else:
            pc.printout("Export to JSON: ")
            pc.printout("disabled", pc.RED)
            pc.printout("\n")

        self.jsonDump = flag

    def login(self, u, p, session_id=None):
        settings_file = "config/settings.json"
        self.api = Client()
        self.api.handle_exception = self._handle_exception

        logged_in = False

        if session_id:
            try:
                if self.api.login_by_sessionid(session_id):
                    logged_in = True
                    pc.printout("Logged in via session ID\n", pc.GREEN)
            except Exception as e:
                logger.info("Session ID login failed: %s" % e)

        if not logged_in:
            session = None
            if os.path.isfile(settings_file):
                session = self.api.load_settings(settings_file)

            if session:
                try:
                    self.api.set_settings(session)
                    self.api.login(u, p)
                    try:
                        self.api.get_timeline_feed()
                    except LoginRequired:
                        logger.info("Session is invalid, need to login via username and password")
                        old_session = self.api.get_settings()
                        self.api.set_settings({})
                        self.api.set_uuids(old_session["uuids"])
                        self.api.login(u, p)
                    logged_in = True
                except Exception as e:
                    logger.info("Couldn't login user using session information: %s" % e)

            if not logged_in:
                try:
                    logger.info("Attempting to login via username and password. username: %s" % u)
                    if self.api.login(u, p):
                        logged_in = True
                except Exception as e:
                    logger.info("Couldn't login user using username and password: %s" % e)

        if not logged_in:
            pc.printout("Couldn't login with either password, session, or session ID\n", pc.RED)
            exit(9)

        self.api.dump_settings(settings_file)

    def _handle_exception(self, client, e):
        if isinstance(e, BadPassword):
            client.logger.exception(e)
            if client.relogin_attempt > 0:
                raise ReloginAttemptExceeded(e)
            raise
        elif isinstance(e, LoginRequired):
            client.logger.exception(e)
            client.relogin()
            return True
        elif isinstance(e, ChallengeRequired):
            api_path = json_value(client.last_json, "challenge", "api_path")
            if api_path == "/challenge/":
                logger.warning("Generic challenge flow requires manual handling")
            else:
                try:
                    client.challenge_resolve(client.last_json)
                except (ChallengeRequired, SelectContactPointRecoveryForm, RecaptchaChallengeForm) as exc:
                    raise exc
            return True
        elif isinstance(e, FeedbackRequired):
            message = client.last_json.get("feedback_message", "")
            logger.warning("Action blocked by Instagram: %s", message)
        elif isinstance(e, ClientThrottledError):
            logger.warning("HTTP 429 from Instagram, back off and review proxy/account pressure")
        elif isinstance(e, PleaseWaitFewMinutes):
            logger.warning("Please wait before retrying: %s", e)
        raise e

    def check_following(self):
        if self.target_id == self.api.user_id:
            return True
        try:
            friendship = self.api.user_friendship_status(self.target_id)
            return friendship.following
        except Exception:
            return False

    def check_private_profile(self):
        if self.is_private and not self.following:
            pc.printout("Impossible to execute command: user has private profile\n", pc.RED)
            send = input("Do you want send a follow request? [Y/N]: ")
            if send.lower() == "y":
                self.api.user_follow(self.target_id)
                print("Sent a follow request to target. Use this command after target accepting the request.")

            return True
        return False

    def get_fwersemail(self):
        if self.check_private_profile():
            return

        followers = []

        try:
            pc.printout("Searching for emails of target followers... this can take a few minutes\n")

            followers_dict = self.api.user_followers(self.target_id)

            for uid, user_short in followers_dict.items():
                u = {
                    'id': user_short.pk,
                    'username': user_short.username,
                    'full_name': user_short.full_name or ''
                }
                followers.append(u)

            pc.printout("\nFound " + str(len(followers)) + " followers\n", pc.GREEN)

            results = []
            
            pc.printout("Do you want to get all emails? y/n: ", pc.YELLOW)
            value = input()
            
            if value == str("y") or value == str("yes") or value == str("Yes") or value == str("YES"):
                value = len(followers)
            elif value == str(""):
                print("\n")
                return
            elif value == str("n") or value == str("no") or value == str("No") or value == str("NO"):
                while True:
                    try:
                        pc.printout("How many emails do you want to get? ", pc.YELLOW)
                        new_value = int(input())
                        value = new_value - 1
                        break
                    except ValueError:
                        pc.printout("Error! Please enter a valid integer!", pc.RED)
                        print("\n")
                        return
            else:
                pc.printout("Error! Please enter y/n :-)", pc.RED)
                print("\n")
                return

            for follow in followers:
                user = self.api.user_info(str(follow['id']))
                if user.public_email:
                    follow['email'] = user.public_email
                    if len(results) > value:
                        break
                    results.append(follow)

        except ClientThrottledError  as e:
            pc.printout("\nError: Instagram blocked the requests. Please wait a few minutes before you try again.", pc.RED)
            pc.printout("\n")

        if len(results) > 0:

            t = PrettyTable(['ID', 'Username', 'Full Name', 'Email'])
            t.align["ID"] = "l"
            t.align["Username"] = "l"
            t.align["Full Name"] = "l"
            t.align["Email"] = "l"

            json_data = {}

            for node in results:
                t.add_row([str(node['id']), node['username'], node['full_name'], node['email']])

            if self.writeFile:
                file_name = self.output_dir + "/" + self.target + "_fwersemail.txt"
                file = open(file_name, "w")
                file.write(str(t))
                file.close()

            if self.jsonDump:
                json_data['followers_email'] = results
                json_file_name = self.output_dir + "/" + self.target + "_fwersemail.json"
                with open(json_file_name, 'w') as f:
                    json.dump(json_data, f)

            print(t)
        else:
            pc.printout("Sorry! No results found :-(\n", pc.RED)

    def get_fwingsemail(self):
        if self.check_private_profile():
            return

        followings = []

        try:

            pc.printout("Searching for emails of users followed by target... this can take a few minutes\n")

            following_dict = self.api.user_following(self.target_id)

            for uid, user_short in following_dict.items():
                u = {
                    'id': user_short.pk,
                    'username': user_short.username,
                    'full_name': user_short.full_name or ''
                }
                followings.append(u)

            results = []
            
            pc.printout("Do you want to get all emails? y/n: ", pc.YELLOW)
            value = input()
            
            if value == str("y") or value == str("yes") or value == str("Yes") or value == str("YES"):
                value = len(followings)
            elif value == str(""):
                print("\n")
                return
            elif value == str("n") or value == str("no") or value == str("No") or value == str("NO"):
                while True:
                    try:
                        pc.printout("How many emails do you want to get? ", pc.YELLOW)
                        new_value = int(input())
                        value = new_value - 1
                        break
                    except ValueError:
                        pc.printout("Error! Please enter a valid integer!", pc.RED)
                        print("\n")
                        return
            else:
                pc.printout("Error! Please enter y/n :-)", pc.RED)
                print("\n")
                return

            for follow in followings:
                sys.stdout.write("\rCatched %i followings email" % len(results))
                sys.stdout.flush()
                user = self.api.user_info(str(follow['id']))
                if user.public_email:
                    follow['email'] = user.public_email
                    if len(results) > value:
                        break
                    results.append(follow)
        
        except ClientThrottledError as e:
            pc.printout("\nError: Instagram blocked the requests. Please wait a few minutes before you try again.", pc.RED)
            pc.printout("\n")
        
        print("\n")

        if len(results) > 0:
            t = PrettyTable(['ID', 'Username', 'Full Name', 'Email'])
            t.align["ID"] = "l"
            t.align["Username"] = "l"
            t.align["Full Name"] = "l"
            t.align["Email"] = "l"

            json_data = {}

            for node in results:
                t.add_row([str(node['id']), node['username'], node['full_name'], node['email']])

            if self.writeFile:
                file_name = self.output_dir + "/" + self.target + "_fwingsemail.txt"
                file = open(file_name, "w")
                file.write(str(t))
                file.close()

            if self.jsonDump:
                json_data['followings_email'] = results
                json_file_name = self.output_dir + "/" + self.target + "_fwingsemail.json"
                with open(json_file_name, 'w') as f:
                    json.dump(json_data, f)

            print(t)
        else:
            pc.printout("Sorry! No results found :-(\n", pc.RED)

    def get_fwingsnumber(self):
        if self.check_private_profile():
            return

        try:
            pc.printout("Searching for phone numbers of users followed by target... this can take a few minutes\n")

            followings = []

            following_dict = self.api.user_following(self.target_id)

            for uid, user_short in following_dict.items():
                u = {
                    'id': user_short.pk,
                    'username': user_short.username,
                    'full_name': user_short.full_name or ''
                }
                followings.append(u)

            results = []
        
            pc.printout("Do you want to get all phone numbers? y/n: ", pc.YELLOW)
            value = input()
            
            if value == str("y") or value == str("yes") or value == str("Yes") or value == str("YES"):
                value = len(followings)
            elif value == str(""):
                print("\n")
                return
            elif value == str("n") or value == str("no") or value == str("No") or value == str("NO"):
                while True:
                    try:
                        pc.printout("How many phone numbers do you want to get? ", pc.YELLOW)
                        new_value = int(input())
                        value = new_value - 1
                        break
                    except ValueError:
                        pc.printout("Error! Please enter a valid integer!", pc.RED)
                        print("\n")
                        return
            else:
                pc.printout("Error! Please enter y/n :-)", pc.RED)
                print("\n")
                return

            for follow in followings:
                sys.stdout.write("\rCatched %i followings phone numbers" % len(results))
                sys.stdout.flush()
                user = self.api.user_info(str(follow['id']))
                if user.contact_phone_number:
                    follow['contact_phone_number'] = user.contact_phone_number
                    if len(results) > value:
                        break
                    results.append(follow)

        except ClientThrottledError as e:
            pc.printout("\nError: Instagram blocked the requests. Please wait a few minutes before you try again.", pc.RED)
            pc.printout("\n")
        
        print("\n")

        if len(results) > 0:
            t = PrettyTable(['ID', 'Username', 'Full Name', 'Phone'])
            t.align["ID"] = "l"
            t.align["Username"] = "l"
            t.align["Full Name"] = "l"
            t.align["Phone number"] = "l"

            json_data = {}

            for node in results:
                t.add_row([str(node['id']), node['username'], node['full_name'], node['contact_phone_number']])

            if self.writeFile:
                file_name = self.output_dir + "/" + self.target + "_fwingsnumber.txt"
                file = open(file_name, "w")
                file.write(str(t))
                file.close()

            if self.jsonDump:
                json_data['followings_phone_numbers'] = results
                json_file_name = self.output_dir + "/" + self.target + "_fwingsnumber.json"
                with open(json_file_name, 'w') as f:
                    json.dump(json_data, f)

            print(t)
        else:
            pc.printout("Sorry! No results found :-(\n", pc.RED)

    def get_fwersnumber(self):
        if self.check_private_profile():
            return

        followings = []

        try:

            pc.printout("Searching for phone numbers of users followers... this can take a few minutes\n")

            followers_dict = self.api.user_followers(self.target_id)

            for uid, user_short in followers_dict.items():
                u = {
                    'id': user_short.pk,
                    'username': user_short.username,
                    'full_name': user_short.full_name or ''
                }
                followings.append(u)

            results = []
            
            pc.printout("Do you want to get all phone numbers? y/n: ", pc.YELLOW)
            value = input()
            
            if value == str("y") or value == str("yes") or value == str("Yes") or value == str("YES"):
                value = len(followings)
            elif value == str(""):
                print("\n")
                return
            elif value == str("n") or value == str("no") or value == str("No") or value == str("NO"):
                while True:
                    try:
                        pc.printout("How many phone numbers do you want to get? ", pc.YELLOW)
                        new_value = int(input())
                        value = new_value - 1
                        break
                    except ValueError:
                        pc.printout("Error! Please enter a valid integer!", pc.RED)
                        print("\n")
                        return
            else:
                pc.printout("Error! Please enter y/n :-)", pc.RED)
                print("\n")
                return

            for follow in followings:
                sys.stdout.write("\rCatched %i followers phone numbers" % len(results))
                sys.stdout.flush()
                user = self.api.user_info(str(follow['id']))
                if user.contact_phone_number:
                    follow['contact_phone_number'] = user.contact_phone_number
                    if len(results) > value:
                        break
                    results.append(follow)

        except ClientThrottledError as e:
            pc.printout("\nError: Instagram blocked the requests. Please wait a few minutes before you try again.", pc.RED)
            pc.printout("\n")

        print("\n")

        if len(results) > 0:
            t = PrettyTable(['ID', 'Username', 'Full Name', 'Phone'])
            t.align["ID"] = "l"
            t.align["Username"] = "l"
            t.align["Full Name"] = "l"
            t.align["Phone number"] = "l"

            json_data = {}

            for node in results:
                t.add_row([str(node['id']), node['username'], node['full_name'], node['contact_phone_number']])

            if self.writeFile:
                file_name = self.output_dir + "/" + self.target + "_fwersnumber.txt"
                file = open(file_name, "w")
                file.write(str(t))
                file.close()

            if self.jsonDump:
                json_data['followings_phone_numbers'] = results
                json_file_name = self.output_dir + "/" + self.target + "_fwerssnumber.json"
                with open(json_file_name, 'w') as f:
                    json.dump(json_data, f)

            print(t)
        else:
            pc.printout("Sorry! No results found :-(\n", pc.RED)

    def get_comments(self):
        if self.check_private_profile():
            return

        pc.printout("Searching for users who commented...\n")

        data = self.__get_feed__()
        users = []

        for post in data:
            comments = self.__get_comments__(post['id'])
            for comment in comments:
                print(comment['text'])
                
                # if not any(u['id'] == comment['user']['pk'] for u in users):
                #     user = {
                #         'id': comment['user']['pk'],
                #         'username': comment['user']['username'],
                #         'full_name': comment['user']['full_name'],
                #         'counter': 1
                #     }
                #     users.append(user)
                # else:
                #     for user in users:
                #         if user['id'] == comment['user']['pk']:
                #             user['counter'] += 1
                #             break

        if len(users) > 0:
            ssort = sorted(users, key=lambda value: value['counter'], reverse=True)

            json_data = {}

            t = PrettyTable()

            t.field_names = ['Comments', 'ID', 'Username', 'Full Name']
            t.align["Comments"] = "l"
            t.align["ID"] = "l"
            t.align["Username"] = "l"
            t.align["Full Name"] = "l"

            for u in ssort:
                t.add_row([str(u['counter']), u['id'], u['username'], u['full_name']])

            print(t)

            if self.writeFile:
                file_name = self.output_dir + "/" + self.target + "_users_who_commented.txt"
                file = open(file_name, "w")
                file.write(str(t))
                file.close()

            if self.jsonDump:
                json_data['users_who_commented'] = ssort
                json_file_name = self.output_dir + "/" + self.target + "_users_who_commented.json"
                with open(json_file_name, 'w') as f:
                    json.dump(json_data, f)
        else:
            pc.printout("Sorry! No results found :-(\n", pc.RED)

    def clear_cache(self):
        try:
            f = open("config/settings.json",'w')
            f.write("{}")
            pc.printout("Cache Cleared.\n",pc.GREEN)
        except FileNotFoundError:
            pc.printout("Settings.json don't exist.\n",pc.RED)
        finally:
            f.close()
