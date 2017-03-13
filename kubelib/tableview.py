import logging
import logging.config

LOG = logging.getLogger(__name__)

simple_ascii = "/-v\\+^|><`*:"
fancy_unicode = "\u250C\u2500\u252C\u2510\u253C\u2534\u2502\u251C\u2524\u2514\u2518\u250A"
try:
    trans = "".maketrans(simple_ascii, fancy_unicode)
except AttributeError:
    import string
    # apparantly python2 doesn't get to have nice things.
    trans = string.maketrans(simple_ascii, simple_ascii)

class TableView(object):
    """It's turtles all the way down."""

    def __init__(self, title=None, width="auto", center=False, link=None):
        self.columns = []

        self.title = title
        self.autowidth = width == "auto"

        self.width = width
        self.center = center
        self.link = link
        self.data = []

    def set_format(self):
        if self.center:
            self.title_format = "{:^%i.%i}" % (self.width, self.width)
        else:
            self.title_format = "{:>%i.%i}" % (self.width, self.width)

        self.data_format = "{:>%i.%i}" % (self.width, self.width)

        # print('title_format: %s' % self.title_format)
        # print('data_format: %s' % self.data_format)

    def get_height(self):
        height = 0
        for column in self.columns:
            height = max(1 + column.get_height(), height)
        return height

    def get_title(self, depth=0):
        self.set_format()

        if depth == 0:
            return self.title_format.format(self.title)
        else:
            title = []
            for column in self.columns:
                title.append(column.get_title(depth=depth - 1))

            return self.title_format.format(':'.join(title))

    def get_value(self, data):
        self.set_format()

        value = []
        if self.link and self.link in data:
            value.append(str(data[self.link]))
        elif self.link:
            LOG.debug('could not find %s in %s', self.link, data.keys())
        else:
            LOG.debug('no link for %s', self.title)

        for column in self.columns:
            v = column.get_value(data)
            if v:
                value.append(str(v))
        self.set_format()
        # LOG.info('value: %s', value)
        return self.data_format.format(":".translate(trans).join(value))


    def layout(self, data):
        autowidth = 0
        for column in self.columns:
            column.layout(data)
            autowidth += column.width

        if self.columns:
            autowidth += len(self.columns) - 1

        if autowidth == 0:
            for data_dict in data:
                if self.link in data_dict:
                    autowidth = max(autowidth, len(str(data_dict[self.link])))
                    LOG.debug(
                        "found %s == %s (%s)",
                        self.link, data_dict[self.link],
                        autowidth
                    )
                else:
                    LOG.debug(
                        "could not find %s in %s",
                        self.link, data_dict.keys()
                    )

            # at least 5
            autowidth = max(autowidth, 3)

        if self.autowidth:
            self.width = autowidth

    def add_column(self, column):
        self.columns.append(column)

    def add_columns(self, columns):
        for column in columns:
            self.add_column(column)

    def makebar(self, top=False, bottom=False):
        if top:
            bar = "/"
        elif bottom:
            bar = "`"
        else:
            # side
            bar = ">"

        for index, col in enumerate(self.columns):
            last = (index + 1) == len(self.columns)

            bar += "-" * col.width
            if top:
                if last:
                    bar += "\\"
                else:
                    bar += "v"
            elif bottom:
                if last:
                    bar += "*"
                else:
                    bar += "^"
            else:
                if last:
                    bar += "<"
                else:
                    bar += "+"

        return bar.translate(trans)

    def set_data(self, data):
        """
        Data is expected to be a list of dictionaries, the keys are 'link' strings.
        """
        self.data = data

    def __str__(self):
        self.layout(self.data)

        vbar = "|".translate(trans)

        out = []
        # header
        out.append(self.makebar(top=True))

        # major labels
        height = 0

        # what is the "tallest" column header?
        height = self.get_height()
        # for index, col in enumerate(self.columns):
        #     height = max(col.get_height, height)

        # print('max height: %i' % height)
        for row in range(height):
            # print('row: %i' % row)

            row_string = vbar
            for index, column in enumerate(self.columns):
                # print("Working on %r" % column)

                titles = column.get_title(depth=row)
                row_string += titles + vbar

            out.append(row_string.translate(trans))

        out.append(self.makebar())

        for data_dict in self.data:
            row_string = vbar
            for index, column in enumerate(self.columns):
                values = column.get_value(data_dict)
                row_string += values + vbar
            out.append(row_string)

        out.append(self.makebar(bottom=True))
        return "\n".join(out)


def main(context, namespace_name):

    tv = TableView()
    alpha = TableView('alpha', center=True, link="alpha")
    beta = TableView('beta', center=True, link="beta")
    gamma = TableView('gamma', center=True, link="gamma")

    delta = TableView('delta', center=True)
    epsilon = TableView('epsilon', center=True, link="epsilon")
    delta.add_column(epsilon)

    phi = TableView('phi', center=True)
    chi = TableView('chi', center=True, link="chi")
    psi = TableView('psi', center=True, link="psi")
    phi.add_columns([chi, psi])

    tv.add_columns([alpha, beta, gamma, delta, phi])

    tv.set_data([{
        'alpha': '_alpha1_',
        'beta': '_beta1_',
        'gamma': '_gamma1_',
        'epsilon': '_epsilon1_',
        'chi': '_chi1_',
        'psi': '_psi1_',
    }, {
        'alpha': '_alpha2_',
        'beta': '_beta2_',
        'gamma': '_gamma2_',
        'epsilon': '_epsilon2_',
        'chi': '_chi2_',
        'psi': '_psi2_',
    }, {
        'alpha': '_alpha3_',
        'beta': '_beta3_',
        'gamma': '_gamma3_',
        'epsilon': '_epsilon3_',
        'chi': '_chi3_',
        'psi': '_psi3_',
    }])

    print(tv)


if __name__ == "__main__":
    args = docopt.docopt(__doc__)
    context = args['--context']
    namespace = args['--namespace']

    main(context, namespace)