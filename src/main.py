import flet as ft

def main(page: ft.Page):
    page.title = "档案馆助手"
    page.add(
        ft.Text("恭喜！APK 打包成功！", size=24, color="teal"),
        ft.ElevatedButton("点我试试")
    )

ft.run(main)
