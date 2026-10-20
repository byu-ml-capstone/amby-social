import functools

import bcrypt

# Production keeps bcrypt's default cost (12, ~0.24s per hash/check), which was ~88% of
# the suite's runtime. Tests only need valid hashes, so drop to the minimum cost. This
# package imports before conftest pulls in `app`, so app.auth.DUMMY_HASH is cheap too.
bcrypt.gensalt = functools.partial(bcrypt.gensalt, rounds=4)
