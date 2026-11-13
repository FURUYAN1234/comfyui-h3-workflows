"""Current H3 download and workflow references must match the release metadata."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent

class PublicDownloadLinksTests(unittest.TestCase):
    def setUp(self):
        self.meta=json.loads((ROOT/'VERSION.json').read_text())
        self.stamp=self.meta['built_at_jst']
        self.version=self.meta['version']
        self.tag='h3-split-'+self.stamp
        self.archives={f'H3_{kind}_{self.stamp}_v{self.version}.zip' for kind in ('Video','MV')}
    def test_both_landing_pages_link_current_independent_kits(self):
        for kind,label in [('video','Video'),('mv','MV')]:
            text=(ROOT/f'docs/site/h3-{kind}/demo/index.html').read_text()
            self.assertIn('Local generation · v'+self.version,text)
            links=re.findall(r'href="([^"]+/releases/download/[^"]+)"',text)
            self.assertEqual(len(links),1)
            self.assertTrue(links[0].endswith(self.tag+f'/H3_{label}_{self.stamp}_v{self.version}.zip'))
    def test_active_setup_and_article_archive_names_are_current(self):
        paths=[ROOT/'README.md',ROOT/'REPRODUCE.md',ROOT/'docs/note-article.md',*list((ROOT/'docs/h3').rglob('*.md'))]
        for path in paths:
            names=re.findall(r'H3_(?:Video|MV)_[0-9]{14}_v[0-9.]+\.zip',path.read_text())
            if path.name in {"README.md", "REPRODUCE.md", "note-article.md"}: self.assertTrue(names,str(path))
            self.assertTrue(set(names)<=self.archives,(str(path),names))
        self.assertIn('--stamp '+self.stamp,(ROOT/'REPRODUCE.md').read_text())
    def test_mv_node_guide_references_an_existing_workflow(self):
        text=(ROOT/'custom_nodes/comfyui-mv-workflow/README.md').read_text()
        names=re.findall(r'workflows/(MV_[^`\s]+\.json)',text)
        self.assertTrue(names)
        for name in names:self.assertTrue((ROOT/'workflows'/name).is_file(),name)
    def test_current_license_references_use_current_runtime_version(self):
        for path in ('FURU_TERMS.md','LICENSE_SCOPE.md'):
            text=(ROOT/path).read_text()
            self.assertIn('**H3 v'+self.version+'**',text)

if __name__=='__main__':unittest.main()
