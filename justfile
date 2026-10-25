# the first recipe is the default, so `just bybit` opens a shell
shell:
    pixi shell

test *args:
    pixi run test {{args}}
