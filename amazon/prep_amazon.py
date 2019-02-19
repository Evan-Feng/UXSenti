import xml.etree.ElementTree as ET
import stanfordnlp
import argparse
import os

SRC_DIR = 'cls-acl10-unprocessed/'
LANGS = ['en', 'fr', 'de', 'ja']
DOMAINS = ['books', 'dvd', 'music']
PART = ['train.review', 'test.review', 'unlabeled.review']


def tokenize(tokenizer, doc):
    res = []
    for sent in tokenizer(doc).sentences:
        for tok in sent.tokens:
            t = tok.text.lower()
            t = '<num>' if t.isdigit() else t
            res.append(t)
    return res


def create(path):
    d = os.path.dirname(path)
    if not os.path.exists(d):
        os.makedirs(d)
    with open(path, 'w', encoding='utf-8') as fout:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', default='../data/amazon/', help='output dir')
    parser.add_argument('--cpu', action='store_true', help='use cpu')
    parser.add_argument('-bs', '--batch_size', type=int, default=1024, help='batch size')
    args = parser.parse_args()
    print(str(args))

    for lang in LANGS:
        trg_lang_file = os.path.join(args.output, lang, 'full.review')
        create(trg_lang_file)
        tokenizer = stanfordnlp.Pipeline(lang=lang, processors='tokenize', use_gpu=(not args.cpu), tokenize_batch_size=args.batch_size)

        for dom in DOMAINS:
            for part in PART:
                trg_file = os.path.join(args.output, lang, dom, part)
                create(trg_file)

                root = ET.parse(os.path.join(SRC_DIR, lang, dom, part)).getroot()
                nitem, npos, nneg = 0, 0, 0
                for t in root:
                    try:
                        dic = {x.tag: x.text for x in t}
                        if part == 'unlabeled.review':
                            label = '__unk__'
                        else:
                            label = '__pos__' if float(dic['rating']) > 3 else '__neg__'
                        tokens = tokenize(tokenizer, dic['text'])
                        with open(trg_file, 'a', encoding='utf-8') as fout:
                            fout.write(label + ' ' + ' '.join(tokens) + '\n')
                        with open(trg_lang_file, 'a', encoding='utf-8') as fout:
                            fout.write(' '.join(tokens) + '\n')

                        if label == '__pos__':
                            npos += 1
                        elif label == '__neg__':
                            nneg += 1
                        nitem += 1

                    except Exception as e:
                        print('[ERROR] ignoring item - {}'.format(e))

                print('file: {}   valid: {}   pos: {}   neg: {}'.format(os.path.join(lang, dom, part), nitem, npos, nneg))


if __name__ == '__main__':
    main()