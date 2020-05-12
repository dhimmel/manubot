"""Functions importable from manubot.cite submodule (submodule API):

  standardize_citekey()
  citekey_to_csl_item()

Helpers:

  inspect_citekey()
  is_valid_citekey() - also used in manubot.process
  shorten_citekey() - used solely in manubot.process
  infer_citekey_prefix()

"""
import functools
import logging
import re
import typing
import dataclasses

from manubot.util import import_function


citeproc_retrievers = {
    "doi": "manubot.cite.doi.get_doi_csl_item",
    "pmid": "manubot.cite.pubmed.get_pubmed_csl_item",
    "pmcid": "manubot.cite.pubmed.get_pmc_csl_item",
    "arxiv": "manubot.cite.arxiv.get_arxiv_csl_item",
    "isbn": "manubot.cite.isbn.get_isbn_csl_item",
    "wikidata": "manubot.cite.wikidata.get_wikidata_csl_item",
    "url": "manubot.cite.url.get_url_csl_item",
}

"""
Regex to extract citation keys.
The leading '@' is omitted from the single match group.

Same rules as pandoc, except more permissive in the following ways:

1. the final character can be a slash because many URLs end in a slash.
2. underscores are allowed in internal characters because URLs, DOIs, and
   citation tags often contain underscores.

If a citekey does not match this regex, it can be substituted for a
tag that does, as defined in citation-tags.tsv.

https://github.com/greenelab/manubot-rootstock/issues/2#issuecomment-312153192

Prototyped at https://regex101.com/r/s3Asz3/4
"""
citekey_pattern = re.compile(r"(?<!\w)@([a-zA-Z0-9][\w:.#$%&\-+?<>~/]*[a-zA-Z0-9/])")


@dataclasses.dataclass
class Handler:

    prefix_lower: str
    prefixes = []

    def _get_pattern(self, attribute="accession_pattern") -> typing.Pattern:
        # todo: cache compilation
        pattern = getattr(self, attribute, None)
        if not pattern:
            return None
        if not isinstance(pattern, typing.Pattern):
            pattern = re.compile(pattern)
        return pattern

    def inspect(self, citekey):
        pattern = self._get_pattern("accession_pattern")
        if not pattern:
            return
        if not pattern.fullmatch(citekey.accession):
            return f"{citekey.accession} does not match regex {pattern.pattern}"

    def standardize_prefix_accession(self, accession):
        standard_prefix = getattr(self, "standard_prefix", self.prefix_lower)
        standard_accession = accession
        return standard_prefix, standard_accession

    @staticmethod
    def get_csl_item(citekey):
        return {}


@dataclasses.dataclass
class CiteKey:
    input_id: str
    aliases: dict = dataclasses.field(default_factory=dict)
    # csl_item_id: str = None
    # manual_reference: bool = False
    # manual_reference_id: str = None

    @classmethod
    @functools.lru_cache(maxsize=None)
    def from_input_id(cls, input_id, aliases={}):
        return cls(input_id, aliases)

    @functools.cached_property
    def dealiased_id(self):
        return self.aliases.get(self.input_id, self.input_id)

    def _set_prefix_accession(self):
        try:
            prefix, accession = self.dealiased_id.split(":", 1)
            prefix = prefix.lower()
        except ValueError:
            prefix, accession = None, None
        self._prefix_lower = prefix
        self._accession = accession

    @property
    def prefix_lower(self):
        if not hasattr(self, "_prefix_lower"):
            self._set_prefix_accession()
        return self._prefix_lower

    def inspect(self):
        return self.handler.inspect(self)

    @property
    def accession(self):
        if not hasattr(self, "_accession"):
            self._set_prefix_accession()
        return self._accession

    @property
    def standard_prefix(self):
        if not hasattr(self, "_standard_prefix"):
            self._standardize()
        return self._standard_prefix

    @property
    def standard_accession(self):
        if not hasattr(self, "_standard_accession"):
            self._standardize()
        return self._standard_accession

    @functools.cached_property
    def handler(self):
        from .handlers import get_handler

        try:
            return get_handler(self.prefix_lower)
        except KeyError:
            return Handler(self.prefix_lower)

    def _standardize(self):
        if self.prefix_lower is None:
            self._standard_prefix = None
            self._standard_accession = None
            self._standard_id = self.dealiased_id
            return
        (
            self._standard_prefix,
            self._standard_accession,
        ) = self.handler.standardize_prefix_accession(self.accession)
        self._standard_id = f"{self._standard_prefix}:{self._standard_accession}"

    @property
    def standard_id(self):
        if not hasattr(self, "_standard_id"):
            self._standardize()
        return self._standard_id

    @functools.cached_property
    def short_id(self):
        return shorten_citekey(self.standard_id)

    def __hash__(self):
        return hash((self.input_id, self.dealiased_id))

    def __repr__(self):
        return " --> ".join(
            f"{getattr(self, key)} ({key})"
            for key in (
                "input_id",
                "dealiased_id",
                "prefix_lower",
                "accession",
                "standard_id",
                "short_id",
            )
        )

    # @classmethod
    # def from_citekey(input_id):
    #     pass

    @functools.cached_property
    def csl_item(self):
        self.handler.get_csl_item(self)


@functools.lru_cache(maxsize=5_000)
def standardize_citekey(citekey, warn_if_changed=False):
    """
    Standardize citation keys based on their source
    """
    standard_citekey = CiteKey(citekey).standard_id
    if warn_if_changed and citekey != standard_citekey:
        logging.warning(
            f"standardize_citekey expected citekey to already be standardized.\n"
            f"Instead citekey was changed from {citekey!r} to {standard_citekey!r}"
        )
    return standard_citekey


def inspect_citekey(citekey):
    """
    Check citekeys adhere to expected formats. If an issue is detected a
    string describing the issue is returned. Otherwise returns None.
    """
    citekey = CiteKey(citekey)
    return citekey.inspect()


def is_valid_citekey(
    citekey, allow_tag=False, allow_raw=False, allow_pandoc_xnos=False
):
    """
    Return True if citekey is a properly formatted string. Return False if
    citekey is not a citation or is an invalid citation.

    In the case citekey is invalid, an error is logged. This
    function does not catch all invalid citekeys, but instead performs cursory
    checks, such as ensuring citekeys adhere to the expected formats. No calls to
    external resources are used by these checks, so they will not detect
    citekeys to non-existent identifiers unless those identifiers violate
    their source's syntax.

    allow_tag=False, allow_raw=False, and allow_pandoc_xnos=False enable
    allowing citekey sources that are valid for Manubot manuscripts, but
    likely not elsewhere. allow_tag=True enables citekey tags (e.g.
    tag:citation-tag). allow_raw=True enables raw citekeys (e.g.
    raw:manual-reference). allow_pandoc_xnos=True still returns False for
    pandoc-xnos references (e.g. fig:figure-id), but does not log an error.
    With the default of False for these arguments, valid sources are restricted
    to those for which manubot can retrieve metadata based only on the
    standalone citekey.
    """
    from .handlers import prefix_to_handler

    if not isinstance(citekey, str):
        logging.error(
            f"citekey should be type 'str' not "
            f"{type(citekey).__name__!r}: {citekey!r}"
        )
        return False
    if citekey.startswith("@"):
        logging.error(f"invalid citekey: {citekey!r}\nstarts with '@'")
        return False
    try:
        source, identifier = citekey.split(":", 1)
    except ValueError:
        logging.error(
            f"citekey not splittable via a single colon: {citekey}. "
            "Citekeys must be in the format of `source:identifier`."
        )
        return False

    if not source or not identifier:
        msg = f"invalid citekey: {citekey!r}\nblank source or identifier"
        logging.error(msg)
        return False

    if allow_pandoc_xnos:
        # Exempted non-citation sources used for pandoc-fignos,
        # pandoc-tablenos, and pandoc-eqnos
        pandoc_xnos_keys = {"fig", "tbl", "eq"}
        if source in pandoc_xnos_keys:
            return False
        if source.lower() in pandoc_xnos_keys:
            logging.error(
                f"pandoc-xnos reference types should be all lowercase.\n"
                f'Should {citekey!r} use {source.lower()!r} rather than "{source!r}"?'
            )
            return False

    # Check supported source type
    sources = set(prefix_to_handler)
    if allow_raw:
        sources.add("raw")
    if allow_tag:
        sources.add("tag")
    if source not in sources:
        if source.lower() in sources:
            logging.error(
                f"citekey sources should be all lowercase.\n"
                f'Should {citekey} use "{source.lower()}" rather than "{source}"?'
            )
        else:
            logging.error(
                f"invalid citekey: {citekey!r}\n"
                f"Source {source!r} is not valid.\n"
                f'Valid citation sources are {{{", ".join(sorted(sources))}}}'
            )
        return False

    inspection = inspect_citekey(citekey)
    if inspection:
        logging.error(f"invalid {source} citekey: {citekey}\n{inspection}")
        return False

    return True


def shorten_citekey(standard_citekey):
    """
    Return a shortened citekey derived from the input citekey.
    The input citekey should be standardized prior to this function,
    since differences in the input citekey will result in different shortened citekeys.
    Short citekeys are generated by converting the input citekey to a 6 byte hash,
    and then converting this digest to a base62 ASCII str. Shortened
    citekeys consist of characters in the following ranges: 0-9, a-z and A-Z.
    """
    import hashlib
    import base62

    assert not standard_citekey.startswith("@")
    as_bytes = standard_citekey.encode()
    blake_hash = hashlib.blake2b(as_bytes, digest_size=6)
    digest = blake_hash.digest()
    short_citekey = base62.encodebytes(digest)
    return short_citekey


def citekey_to_csl_item(citekey, prune=True):
    """
    Generate a CSL Item (Python dictionary) for the input citekey.
    """
    from manubot.cite.csl_item import CSL_Item
    from manubot import __version__ as manubot_version

    citekey == standardize_citekey(citekey, warn_if_changed=True)
    source, identifier = citekey.split(":", 1)

    if source not in citeproc_retrievers:
        msg = f"Unsupported citation source {source!r} in {citekey!r}"
        raise ValueError(msg)
    citeproc_retriever = import_function(citeproc_retrievers[source])
    csl_item = citeproc_retriever(identifier)
    csl_item = CSL_Item(csl_item)

    note_text = f"This CSL JSON Item was automatically generated by Manubot v{manubot_version} using citation-by-identifier."
    note_dict = {"standard_id": citekey}
    csl_item.note_append_text(note_text)
    csl_item.note_append_dict(note_dict)

    short_citekey = shorten_citekey(citekey)
    csl_item.set_id(short_citekey)
    csl_item.clean(prune=prune)

    return csl_item


def infer_citekey_prefix(citekey):
    """
    Passthrough citekey if it has a valid citation key prefix. Otherwise,
    if the lowercase citekey prefix is valid, convert the prefix to lowercase.
    Otherwise, assume citekey is raw and prepend "raw:".
    """
    prefixes = [f"{x}:" for x in list(citeproc_retrievers) + ["raw"]]
    for prefix in prefixes:
        if citekey.startswith(prefix):
            return citekey
        if citekey.lower().startswith(prefix):
            return prefix + citekey[len(prefix) :]
    return f"raw:{citekey}"


def url_to_citekey(url):
    """
    Convert a HTTP(s) URL into a citekey.
    For supported sources, convert from url citekey to an alternative source like doi.
    If citekeys fail inspection, revert alternative sources to URLs.
    """
    from urllib.parse import urlparse, unquote

    citekey = None
    parsed_url = urlparse(url)
    domain_levels = parsed_url.hostname.split(".")
    if domain_levels[-2:] == ["doi", "org"]:
        # DOI URLs
        doi = unquote(parsed_url.path.lstrip("/"))
        citekey = f"doi:{doi}"
    if domain_levels[-2] == "sci-hub":
        # Sci-Hub domains
        doi = parsed_url.path.lstrip("/")
        citekey = f"doi:{doi}"
    if domain_levels[-2:] == ["biorxiv", "org"]:
        # bioRxiv URL to DOI. See https://git.io/Je9Hq
        match = re.search(
            r"/(?P<biorxiv_id>([0-9]{4}\.[0-9]{2}\.[0-9]{2}\.)?[0-9]{6,})",
            parsed_url.path,
        )
        if match:
            citekey = f"doi:10.1101/{match.group('biorxiv_id')}"
    is_ncbi_url = parsed_url.hostname.endswith("ncbi.nlm.nih.gov")
    if is_ncbi_url and parsed_url.path.startswith("/pubmed/"):
        # PubMed URLs
        try:
            pmid = parsed_url.path.split("/")[2]
            citekey = f"pmid:{pmid}"
        except IndexError:
            pass
    if is_ncbi_url and parsed_url.path.startswith("/pmc/"):
        # PubMed Central URLs
        try:
            pmcid = parsed_url.path.split("/")[3]
            citekey = f"pmcid:{pmcid}"
        except IndexError:
            pass
    if domain_levels[-2:] == ["wikidata", "org"] and parsed_url.path.startswith(
        "/wiki/"
    ):
        # Wikidata URLs
        try:
            wikidata_id = parsed_url.path.split("/")[2]
            citekey = f"wikidata:{wikidata_id}"
        except IndexError:
            pass
    if domain_levels[-2:] == ["arxiv", "org"]:
        # arXiv identifiers. See https://arxiv.org/help/arxiv_identifier
        try:
            arxiv_id = parsed_url.path.split("/", maxsplit=2)[2]
            if arxiv_id.endswith(".pdf"):
                arxiv_id = arxiv_id[:-4]
            citekey = f"arxiv:{arxiv_id}"
        except IndexError:
            pass
    if citekey is None or inspect_citekey(citekey) is not None:
        citekey = f"url:{url}"
    return citekey
