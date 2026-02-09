***PyPartyRCV - JJ DeFeo***
This is a modified version of pyrcv (see below) which uses the DeFeo Ranked Party List method of voting. 

It is similar to the Australian Senate which uses a form of Party List STV, however, the AU-Sen system uses a shortcut which requires a certain method of choosing candidates. 
The AU-Sen ballot offers either ranking parties, or ranking candidates. If you rank a party "A" as 1st and party "B" as second, behind the scenes what is actually happening is all of the candidats in party A are being ranked randomby (your actual vote (where the order is your rank, letter is party, and number is candidate "name") is: A1,A2,A3...An,B1,B2,B3...Bn) (where n is the # of candidates in the party).

My system directly uses STV on the parties, which required modification of the algorithms, since traditional STV (which pyrcv uses), operates on a threshold basis, with all of your excess...votes being redistributed as soon as you reach enough for one seat (aka a win). This system uses a quota (Droop Quota), then redistributing excess votes beyond won seats for whichever party has the least excess then semi-locking that party so it can no longer recieve excess votes. Then it recounts won seats, then repeating that until there is minimized excess. 
Then, it eliminates the party with the least votes, and redistributes those votes according to (effectively) standard STV procedures, until R=0 where R=Remaining Seats to be Allocated.

This way, you can use any system of choosing candidates' order on the party list that you want.
That is particularly useful if you want to tie primary elections to local districts in some way and order the party list based on those local results enabling local representation under a true proportional electoral system.


***Use Run.bat to start the webserver and open the webpage.***


AI DISCLAIMER: LLM AI, specificially ChatGPT 5.2 and Google Gemini 3 Fast, were used in development of PyPartyRCV in moderate to significant amounts in order to speed up the coding process, as well as correct errors in code and algorithmic logic or loopholes. 
The purpose of this statement is that I believe it is very important for (potential) users to know if the work they are interacting with is AI-assisted/AI-created in order to be able to provide proper informed consent to use/consume content, whether that content be code, images, videos, or any other creative thing.
The idea(s) for this voting system, the math for the system, and the algorithms I invented for this system are all my own creation; I did not use AI until after the coding process began.


Below is the original pyrcv README file. I am very greatful to the creators of pyrcv for having such an amazing, open source tool for RCV/STV, and all of the orgs supporting their work, and work to end regressive, 2-party systems generally so that all political jurisdictions can have representation that truly reflects the needs of their populations.
=====
pyrcv
=====


.. image:: https://img.shields.io/pypi/v/pyrcv.svg
        :target: https://pypi.python.org/pypi/pyrcv
        :alt: PyPi Status

.. image:: https://github.com/chrisroat/pyrcv/actions/workflows/ci.yml/badge.svg
        :target: https://github.com/chrisroat/pyrcv/actions/workflows/ci.yml
        :alt: Test Status

.. image:: https://readthedocs.org/projects/pyrcv/badge/?version=latest
        :target: https://pyrcv.readthedocs.io/en/latest/?version=latest
        :alt: Documentation Status


Python project for adjudicating ranked choice voting elections using the
single transferable vote (STV) method.  For more information on ranked
choice voting, visit the `FairVote website on RCV`_.

The project also contains a small flask server for adjudicating and visualizing
election results from a CSV file.  It is automatically deployed at at `pyrcv.org`_

* Free software: GNU General Public License v3
* Documentation: https://pyrcv.readthedocs.io.


Features
--------

* General standards and APIs for voting data and vote tabulation.
* Tabulation of ranked-choice elections using the single-transferable-vote (STV) method.
* Support for both single-winner and multi-winner contests.
* Generation of Sankey diagram showing the flow of vote counts through the
  rounds of a multi-round election.
* Parser for converting Google Form based election output to voting data standard format.
* Web server which processes CSV data output from a Google Form based election, and
  displays winners and a Sankey diagram.


Credits
-------

Inspired by FairVote_ and CalRCV_.  FairVote's examples were extremely helpful for
development and correctness-testing.

This package was created with Cookiecutter_ and the `audreyr/cookiecutter-pypackage`_ project template.

.. _FairVote website on RCV: https://fairvote.org/our-reforms/ranked-choice-voting/
.. _pyrcv.org: https://www.pyrcv.org
.. _FairVote: https://fairvote.org/
.. _CalRCV: https://www.calrcv.org/
.. _Cookiecutter: https://github.com/audreyr/cookiecutter
.. _`audreyr/cookiecutter-pypackage`: https://github.com/audreyr/cookiecutter-pypackage
