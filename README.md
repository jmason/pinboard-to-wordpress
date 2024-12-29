# pinboard-to-wordpress

Gateway pinboard.in posts to Wordpress -- as used on https://jmason.ie/ to gateway my posts from https://pinboard.in/u:jm .

## How To Use

Copy `envs.example` to `envs` and fill it out with appropriate values.  See that file for details.
Then run:

```
    . envs; python gateway.py
```

Run that as often as necessary; it'll maintain a sqlite database to track which URLs have been previously posted.


## How To Repost An Already-Posted Article

This quick sqlite command line will allow reposting any articles posted in the last 24 hours:

```
sqlite3 already_posted.db  
delete from published_items where published_date > datetime('now','-1 day');
```

