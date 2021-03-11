# NOTICE
# This software was produced for the U.S. Government under contract FA8702-21-C-0001,
# and is subject to the Rights in Data-General Clause 52.227-14, Alt. IV (DEC 2007)
# ©2021 The MITRE Corporation. All Rights Reserved.

'''
This module implements an ontospy wrapper for json-ld data ingest.

1. precondition() embeds line numbers in the json-ld text and
replaces empty prefixes (which are legal turtle but not legal json-ld)
by non-empty ones.

2. ontospy() ingests the modified json-ld file

3. postcondition() removes embedded line numbers from ontospy's graph
(and remembers where they are) undoes the empty prefixes, and
compensates for some of ontospy's deficiencies.
'''
import re
import string
import rdflib

DEFAULT_PREFIX_LENGTH = 3
DEFAULT_ALPHABET = string.ascii_lowercase

def precondition(text, prefix=None):
    '''
    Arguments:
        text   The text (a '\n'-separated string of the json-ld file) to precondition
        prefix  The empty prefix (autogenerated if not specified)

    Return:
        new_text   The preconditioned text (a '\n'-separated string of the json-ld file)

    Preconditions includes:
        Replacing the empty prefix by a provided or self-generated string
        Embedding line numbers in the @types
    '''
    # If prefix is not specified, autogenerate it
    if prefix is None:
        prefix = autogenerate_empty_prefix(text, DEFAULT_PREFIX_LENGTH, DEFAULT_ALPHABET)

    # Replace empty prefix with specified or autogenerated prefix
    text = replace_empty_prefix(text, prefix)

    # Embed line numbers in @type declarations
    text = embed_line_numbers(text)

    # Return preconditioned text
    return text


def postcondition(graph, context):
    '''
    Remove line number from graph constructed from preconditioned file
    and expand apparent prefixes in Literal objects that should be URIs

    Arguments:
        context {prefix:expanded_location}
        graph   A rdflib_graph object

    Return: tuple (new_graph, {node:line_number})
        new_graph   A rdflib_graph object with embedded line numbers removed
        node        An rdflib.term.URIRef or rdflib.term.BNode
        line_number The extracted line number for this node
    '''
    # Start with empty dictionary and empty new graph
    line_numbers = {}
    new_graph = rdflib.Graph()

    # Do for each triple in the original graph
    for subject, predicate, obj in graph.triples((None, None, None)):

        # If obj is a literal and its datatype has a line number
        # remove line number from datatype (do NOT replaces its prefix)
        if isinstance(obj, rdflib.term.Literal) and obj.datatype:
            stripped_string, number = extract_line_number(obj.datatype)
            if number:
                obj = rdflib.term.Literal(str(obj), datatype=stripped_string)
                line_numbers[subject] = number

        # If obj is a literal and has no datatype, it's one of those ambiguous cases
        # If it has an IRI prefix in the context, expand its prefix and replace with a URIRef
        if isinstance(obj, rdflib.term.Literal) and not obj.datatype:
            value = str(obj)
            parts = value.split(':', 1)    # does value look like prefix:stuff?
            if len(parts) > 1 and parts[0] in context:    # yes, and the prefix is in the context!
                parts[0] = context[parts[0]]
                obj = rdflib.term.URIRef(parts[0] + parts[1])

        # If obj is a URIRef (maybe just created above!) and has a line number,
        # remove line number from URIRef object
        if isinstance(obj, rdflib.term.URIRef):
            stripped_string, number = extract_line_number(str(obj))
            if number:
                obj = rdflib.term.URIRef(stripped_string)
                line_numbers[subject] = number

        # If obj is a BNode, leave it alone

        # Add to new_graph
        new_graph.add((subject, predicate, obj))

    return new_graph, line_numbers


# ================================== HELPER FUNCTIONS =================================================

def extract_line_number(text):
    '''
    Arguments:
        text    String that may end with _LINE_n

    Return: Tuple of:
        stripped_text   String with _LINE_n removed if it was present
        number          Line number if _LINE_n was present, else None
    '''
    parts = text.rsplit('_LINE_')
    if len(parts) == 1:
        return text, None
    else:
        return parts[0], int(parts[1])


def autogenerate_empty_prefix(text, prefix_length, alphabet):
    '''
    Arguments:
        text          The text (a '\n'-separated string of the json-ld file) to precondition

    Return:
        An empty_prefix not found anywhere in the text
    '''
    # Find strings in the text that could be prefix strings
    match_string = r'(\w{%s}):' % prefix_length    # e.g., '(\w{3}):'
    non_candidate_prefix_strings = set(re.findall(match_string, text))

    # If we found all possible strings (this is really unlikely),
    # we probably need to increase the string length
    if len(non_candidate_prefix_strings) == len(alphabet):
        raise Exception('Could not find unused prefix sequence!')

    # Look for a prefix string that is not is the non_candidate set
    for prefix in PrefixGenerator(prefix_length, alphabet):
        if prefix not in non_candidate_prefix_strings:
            return prefix

    # We can't be here because of the tests above
    raise Exception('This cannot happen')



def replace_empty_prefix(text, empty_prefix):
    '''
    Arguments:
        text          The text (a '\n'-separated string of the json-ld file) to precondition
        empty_prefix  The empty prefix (autogenerated if not specified)

    Return:
        new_text   The text ('\n'-separated string) with empty prefixes replaced

    Simplifying assumptions:
        The *right* way to do this is to look for empty prefixes in all the json tokens,
        following lists and recursive json objects, and being careful to distinguish
        text/comments from data.

        This function uses a simpler but sloppier approach, based on the example json-ld files.
        It operates directly on the text with the following assumptions.

        1. A @context line defining the empty prefix looks like this (including the three double-quotes):
                 "":<s>"<h>://
              where  <s> is zero or more spaces
                     <h> is one of http, https, or file

        2: A token that has an empty prefix looks like this (including the two double-quotes):
                 ":<x>"
              where <x> is at least one character in [a-zA-Z0-9_-]

        If we find that violates these assumptions, we will have to refine this approach.
    '''
    # 1. Look for the empty-string @context and substitute the replacement prefix
    #    If not found, return the original text unchanged
    #    If more than one found, we've got a problem
    pattern = r'"":(\s*"(?:http|https|file)://)'
    text, count = re.subn(
        pattern,
        lambda m: '"{}":{}'.format(empty_prefix, m.group(1)),
        text)
    if not count:   # If count is zero, no occurrences were found
        return text
    if count > 1:   # This simple approach cannot handle mulitple occurrences
        raise Exception('Found multiple tokens that look like empty-string contexts')

    # 2. Look for apparent empty-prefixed iri
    pattern = r'":([\w-]+)"'     # [\w] is [A-Za-z0-9_]
    text, count = re.subn(
        pattern,
        lambda m: '"{}:{}"'.format(empty_prefix, m.group(1)),
        text)

    # Return modified text
    return text


def embed_line_numbers(text):
    '''
    Arguments:
        text   The text (a '\n'-separated string of the json-ld file) to precondition

    Return:
        A single '\n'-separated string consisting of the input text
           with "_LINE_n" appended to all @type values,
           where n is the (possibly multidigit) line number
    '''
    # This regular expressions matches a @type declaration in the json-ld file
    type_matcher = re.compile(r'( *"@type": *")([\w:#/-]+)(",)')

    # Do for each line in text
    lines = text.split('\n')
    for line_number, line in enumerate(lines):

        # If the line is a @type statement...
        matches = type_matcher.match(line)
        if matches:

            # Replace that line with a new line with _LINE_n appended to the type
            lines[line_number] = '{}{}_LINE_{}{}'.format(
                matches.group(1),
                matches.group(2),
                line_number+1,    # enumerate() returns 0-based line_number, we want 1-based
                matches.group(3))

    # Reconstruct and return the '\n'-separated text with modified @types
    return '\n'.join(lines)



class PrefixGenerator:
    '''
    Prefix string generator.

    Usage:
        for string in PrefixGenerator():   # assuming default parameters (see __init__())
            process(string)
    '''
    def __init__(self, prefix_length, alphabet):
        '''
        Create and initialize the prefix string generator

        Arguments:
            prefix_length   The number of characters in the prefix (please keep this small)
            alphabet        A string of the characters that can appear in the prefix
        '''
        self.prefix_length = prefix_length
        self.alphabet = alphabet
        self.alphabet_length = len(alphabet)
        self.max_count = self.alphabet_length**prefix_length

    def __iter__(self):
        '''
        The generator that yields successive strings
        '''
        for count in range(self.max_count):
            digits = [0]*self.prefix_length
            position = self.prefix_length
            while count:
                position -= 1
                digits[position] = count % self.alphabet_length
                count //= self.alphabet_length
            yield ''.join(self.alphabet[c] for c in digits)




# FOR DEBUGGING
if __name__ == '__main__':
    import sys
    import argparse

    def main():
        '''
        Command line invocation:  precondition [-o output_filepath] jsonld_filepath

        If -o is not specified, write to stdout.
        '''
        # Parse arguments
        parser = argparse.ArgumentParser(sys.argv[0])

        # Optional output filepath
        parser.add_argument(
            '-o',
            type=str,
            dest='output_filepath',
            action='store',
            required=False,
            default=None,
            help='output filepath (stdout if not specified)')

        # Optional prefix
        parser.add_argument(
            '-p',
            type=str,
            dest='prefix',
            action='store',
            required=False,
            default=None,
            help='prefix (autogenerated if not specified)')

        # REQUIRED POSITIONAL ARGUMENT
        parser.add_argument(
            type=str,
            dest='jsonld_filepath',
            help='full path json-ld file to be preconditioned')

        # Parse command line
        args = parser.parse_args(sys.argv[1:])

        # Read the input file
        text = open(args.jsonld_filepath).read()

        # Precondition
        text = precondition(text, args.prefix)

        # Write results
        if args.output_filepath is None:
            print(text)
        else:
            with open(args.output_filepath, 'w') as outfile:
                print(text, file=outfile)

    main()
