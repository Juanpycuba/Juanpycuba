# TopSupergroupsBot - A telegram bot for telegram public groups leaderboards
# Copyright (C) 2017-2018  Dario <dariomsn@hotmail.it> (github.com/91DarioDev)
#
# TopSupergroupsBot is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# TopSupergroupsBot is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with TopSupergroupsBot.  If not, see <http://www.gnu.org/licenses/>.

import time
import json

from topsupergroupsbot import database
from topsupergroupsbot import leaderboards

from telegram.ext.dispatcher import run_async

CACHE_SECONDS = 60*3

CACHE_KEY = 'cached_groups_rank'
BY_MESSAGES = 'by_messages'
BY_MEMBERS = 'by_members'
BY_VOTES = 'by_votes'
RANK = 'rank'
CACHED_AT = 'cached_at'
REGION = 'region'
VALUE = 'value'


def filling_dict(dct_name, group_id, by, position, region, cached_at, value):
    data = {RANK: position, CACHED_AT: cached_at, REGION: region, VALUE: value}
    try:
        dct_name[group_id][by] = data
    except KeyError:
        dct_name[group_id] = {}
        dct_name[group_id][by] = data
    return dct_name


@run_async
def caching_ranks(bot, job):
    #############
    # MESSAGES
    ############

    query = """
        SELECT 
            group_id,
            COUNT(msg_id) AS msgs, 
            RANK() OVER(PARTITION BY s.lang ORDER BY COUNT(msg_id) DESC),
            s.lang
        FROM messages 
        LEFT OUTER JOIN supergroups as s 
        USING (group_id)
        WHERE 
            message_date > date_trunc('week', now())
            AND (s.banned_until IS NULL OR s.banned_until < now()) 
            AND s.bot_inside IS TRUE
        GROUP BY s.lang, group_id
    
    """
    msgs_this_week = database.query_r(query)


    ##################
    #   MEMBERS
    ##################

    query = """
         SELECT
            last_members.group_id,
            last_members.amount, 
            RANK() OVER(PARTITION BY s.lang ORDER BY last_members.amount DESC),
            s.lang,
            extract(epoch from last_members.updated_date at time zone 'utc')
        FROM
            (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY group_id
                    ORDER BY updated_date DESC
                    ) AS row
            FROM members
        ) AS last_members 
        LEFT OUTER JOIN supergroups AS s 
        USING (group_id)
        WHERE 
            last_members.row=1
            AND (s.banned_until IS NULL OR s.banned_until < now())
            AND s.bot_inside IS TRUE
    """
    members_this_week = database.query_r(query)

    ####################
    # SUM AND AVG VOTES
    ####################

    query = """
        WITH myconst AS
        (SELECT 
            s.lang,
            AVG(vote)::float AS overall_avg
        FROM votes AS v
        LEFT OUTER JOIN supergroups AS s
        ON s.group_id = v.group_id
        WHERE (s.banned_until IS NULL OR s.banned_until < now() )
        AND s.bot_inside IS TRUE
        GROUP BY s.lang
        HAVING COUNT(vote) >= %s)

        SELECT 
          *,
          RANK() OVER (PARTITION BY sub.lang  ORDER BY bayesan DESC)
          FROM (
            SELECT 
                v.group_id,
                s_ref.title, 
                s_ref.username, 
                COUNT(vote) AS amount, 
                ROUND(AVG(vote), 1)::float AS average,
                s.nsfw,
                extract(epoch from s.joined_the_bot at time zone 'utc') AS dt,
                s.lang,
                s.category,
                -- (WR) = (v ?? (v+m)) ?? R + (m ?? (v+m)) ?? C
                --    * R = average for the movie (mean) = (Rating)
                --    * v = number of votes for the movie = (votes)
                --    * m = minimum votes required to be listed in the Top 250 (currently 1300)
                --    * C = the mean vote across the whole report (currently 6.8)
                (  (COUNT(vote)::float / (COUNT(vote)+%s)) * AVG(vote)::float + (%s::float / (COUNT(vote)+%s)) * (m.overall_avg) ) AS bayesan
            FROM votes AS v
            LEFT OUTER JOIN supergroups_ref AS s_ref
            ON s_ref.group_id = v.group_id
            LEFT OUTER JOIN supergroups AS s
            ON s.group_id = v.group_id
            LEFT OUTER JOIN myconst AS m
            ON (s.lang = m.lang)
            GROUP BY v.group_id, s_ref.title, s_ref.username, s.nsfw, s.banned_until, s.lang, s.category, s.bot_inside, s.joined_the_bot, m.overall_avg
            HAVING 
                (s.banned_until IS NULL OR s.banned_until < now()) 
                AND COUNT(vote) >= %s
                AND s.bot_inside IS TRUE
          ) AS sub;
    """
    this_week_votes_avg = database.query_r(
        query, 
        leaderboards.VotesLeaderboard.MIN_REVIEWS,
        leaderboards.VotesLeaderboard.MIN_REVIEWS,
        leaderboards.VotesLeaderboard.MIN_REVIEWS,
        leaderboards.VotesLeaderboard.MIN_REVIEWS,
        leaderboards.VotesLeaderboard.MIN_REVIEWS
    )

    dct = {}
    for group in msgs_this_week:
        dct = filling_dict(dct, group[0], BY_MESSAGES, group[2], group[3], time.time(), group[1])

    for group in members_this_week:
        dct = filling_dict(dct, group[0], BY_MEMBERS, group[2], group[3], group[4], group[1])

    for group in this_week_votes_avg:
        dct = filling_dict(dct, group[0], BY_VOTES, group[10], group[7], time.time(), [group[4], group[3]])

    # encoding
    encoded_dct = {k: json.dumps(v).encode('UTF-8') for k,v in dct.items()}
    database.REDIS.hmset(CACHE_KEY, encoded_dct)
    database.REDIS.expire(CACHE_KEY, CACHE_SECONDS*4)
    remove_old_cached_keys(dct)


def get_group_cached_rank(group_id):
    """
    returns:None or a dictionary like:
    {
        'by_messages':
            {
                'rank': 1,
                'cached_at': 1510106982.4582865,
                'region':
                'it'
            },
        'by_members':
            {
                'rank': 1,
                'cached_at': 1510106982.4582865,
                'region': 'it'
            },
        'by_votes':
            {
                'rank': 1,
                'cached_at': 1510106982.4582865,
                'region': 'it'
            }
    }
    """
    rank = database.REDIS.hmget(CACHE_KEY, group_id)[0]
    return json.loads(rank.decode('UTF-8')) if rank is not None else None


def remove_old_cached_keys(new_cache_dct):
    new_groups_list = [i for i in new_cache_dct]
    old_cache = database.REDIS.hgetall(CACHE_KEY)
    old_cached_groups_list = [int(i.decode('UTF-8')) for i in old_cache]
    groups_to_remove = [i for i in old_cached_groups_list if i not in new_groups_list]
    if len(groups_to_remove) > 0:  # to avoid to run hdel with one param
        database.REDIS.hdel(CACHE_KEY, *groups_to_remove)
    
