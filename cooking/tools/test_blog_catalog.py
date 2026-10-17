import unittest
from blog_catalog import parse_page, row_from_facts, dietary_flags, apply_choices, directions, SOURCES
from classify_diet import screen
from fetch_blog_images import pick, direct


class CatalogTests(unittest.TestCase):
    def test_old_ingredient_block_preserves_inline_links(self):
        page = '<h1>Test salad</h1><div class="entry-content"><p>1 cup <a>peas</a><br>Olive oil<br>Salt<br>2 lemons</p><p>Mix everything and serve.</p></div>'
        facts = parse_page(page, 'https://smittenkitchen.com/2000/01/test/', 'smitten-kitchen')
        self.assertEqual(facts['ingredients'], ['1 cup peas', 'Olive oil', 'Salt', '2 lemons'])
        self.assertNotIn('Mix everything', str(facts))

    def test_ingredients_outside_malformed_wrapper_are_recovered(self):
        page = '<h1>Test beans</h1><div class="entry-content"><div class="jetpack-recipe"><h3>Test beans</h3></div></div><ul><li class="jetpack-recipe-ingredient">2 cups beans</li></ul>'
        facts = parse_page(page, 'https://smittenkitchen.com/2000/01/test/', 'smitten-kitchen')
        self.assertEqual(facts['ingredients'], ['2 cups beans'])

    def test_related_links_and_second_recipe_are_not_ingredients(self):
        page = '<h1>Test soup</h1><div class="entry-content"><p>One year ago: Cake<br>Two years ago: Chicken</p><p>2 cups beans<br>1 cup water</p><p>Simmer until done.</p><p>1 pound tuna<br>2 eggs</p></div>'
        facts = parse_page(page, 'https://smittenkitchen.com/2000/01/test/', 'smitten-kitchen')
        self.assertEqual(facts['ingredients'], ['2 cups beans', '1 cup water'])

    def test_directions_in_same_paragraph_are_not_ingredients(self):
        page = '<h1>Test pasta</h1><div class="entry-content"><p>Serves 4<br>1 pound pasta<br>1 lemon<br>Preheat the oven to 400 degrees.</p></div>'
        facts = parse_page(page, 'https://smittenkitchen.com/2000/01/test/', 'smitten-kitchen')
        self.assertEqual(facts['ingredients'], ['1 pound pasta', '1 lemon'])

    def test_jsonld_graph_preserves_facts_but_not_prose(self):
        page = '<script type="application/ld+json">{"@graph":[{"@type":"Recipe","name":"Test rice","recipeIngredient":["1 &frac12; cups rice"],"recipeYield":["4","4 servings"],"totalTime":"PT1H15M","recipeCategory":"Main Dish","description":"Publisher prose","recipeInstructions":[{"text":"Publisher instructions"}]}]}</script>'
        facts = parse_page(page, SOURCES['cookie-and-kate']['seed'], 'cookie-and-kate')
        row = row_from_facts(facts, {'ingredient_tags':['rice'], 'item_tags':[]})
        self.assertEqual(row['ingredients'], ['1 ½ cups rice'])
        self.assertEqual((row['servings'], row['yield_count']), ('4', 4))
        self.assertEqual(row['cook_time'], '1 hour 15 minutes')
        self.assertEqual(row['course_tags'], ['Dinner'])
        self.assertEqual(row['steps'], [])
        self.assertNotIn('Publisher', str(row))

    def test_vegetarian_screen_keeps_oyster_mushrooms_but_holds_fish(self):
        self.assertEqual(dietary_flags({'ingredients':['1 pound oyster mushrooms', '¼ cup coconut bacon',
            '1 tsp fish sauce', '1 tablespoon Worcestershire sauce']}), [])
        for line in ('2 anchovy fillets', 'bonito flakes', '2 cups chicken or vegetable broth', 'bacon (optional)'):
            self.assertEqual(dietary_flags({'ingredients':[line]}), [line])

    def test_fractional_yield_does_not_invent_servings(self):
        facts = {'title':'Test sauce', 'ingredients':['1 cup water'], 'yield_text':'1 1/2 cups',
            'cook_time':None, 'categories':['Sauce'], 'cuisines':[], 'source':'cookie-and-kate',
            'url':'https://cookieandkate.com/test/'}
        row = row_from_facts(facts, {'ingredient_tags':[], 'item_tags':[]})
        self.assertIsNone(row['yield_count'])
        self.assertIsNone(row['servings'])

    def test_primary_recipe_does_not_take_ingredients_from_second_card(self):
        page = '<h1>Soup</h1><div class="entry-content"><div class="jetpack-recipe"><li class="jetpack-recipe-ingredient">2 cups beans</li></div><div class="jetpack-recipe"><li class="jetpack-recipe-ingredient">1 pound tuna</li></div></div>'
        facts = parse_page(page, 'https://smittenkitchen.com/2000/01/test/', 'smitten-kitchen')
        self.assertEqual(facts['ingredients'], ['2 cups beans'])

    def test_explicit_servings_take_precedence_over_slice_count(self):
        facts = {'title':'Test pizza', 'ingredients':['1 cup flour'], 'yield_text':'8 slices, serves 4',
            'cook_time':None, 'categories':['Dinner'], 'cuisines':[], 'source':'smitten-kitchen',
            'url':'https://smittenkitchen.com/2000/01/test/'}
        row = row_from_facts(facts, {'ingredient_tags':[], 'item_tags':[]})
        self.assertEqual((row['servings'], row['yield_count']), ('4', 4))

    def test_vegetarian_choices_must_match_current_ingredients(self):
        facts = {'url':'https://example.com/recipe', 'ingredients':['2 cups chicken or vegetable broth']}
        choices = {facts['url']:{'replacements':{'2 cups chicken or vegetable broth':'2 cups vegetable broth'},'note':'Vegetable option'}}
        selected = apply_choices(facts, choices)
        self.assertEqual(selected['ingredients'], ['2 cups vegetable broth'])
        self.assertEqual(facts['ingredients'], ['2 cups chicken or vegetable broth'])
        self.assertEqual(dietary_flags(selected), [])
        with self.assertRaises(ValueError): apply_choices({**facts,'ingredients':['3 cups chicken or vegetable broth']}, choices)

    def test_photo_prefers_share_image_then_featured_then_post_body(self):
        og = {'yoast_head_json':{'og_image':[{'url':'https://cookieandkate.com/images/2026/10/share.jpg'}]}}
        featured = {'jetpack_featured_media_url':'https://i0.wp.com/smittenkitchen.com/wp-content/uploads/lead.jpg?fit=2560%2C1707'}
        body = {'content':{'rendered':'<p><img class="emoji" src="https://s.w.org/images/core/emoji/smile.png"><img src="https://cookieandkate.com/images/2012/05/kale.jpg" alt=""></p>'}}
        self.assertEqual(pick({**og, **featured, **body}), (og['yoast_head_json']['og_image'][0]['url'], 'og_image'))
        self.assertEqual(pick({**featured, **body}), (featured['jetpack_featured_media_url'], 'featured'))
        self.assertEqual(pick(body), ('https://cookieandkate.com/images/2012/05/kale.jpg', 'content'))
        self.assertEqual(pick({}), (None, None))

    def test_jetpack_cdn_urls_download_from_the_publisher(self):
        self.assertEqual(direct('https://i0.wp.com/smittenkitchen.com/wp-content/uploads//2017/05/chickpeas.jpg?fit=5076%2C3384&ssl=1'),
            'https://smittenkitchen.com/wp-content/uploads/2017/05/chickpeas.jpg')
        self.assertEqual(direct('https://cookieandkate.com/images/2026/10/lentil-curry-scaled.jpg'),
            'https://cookieandkate.com/images/2026/10/lentil-curry-scaled.jpg')

    def test_jetpack_directions_survive_a_stray_closing_paragraph(self):
        # Smitten Kitchen cards close the directions div with </p>; html.parser stopped at step 1.
        body = ('<div class="jetpack-recipe"><div class="jetpack-recipe-directions">Melt the butter.</p>'
            '<p>Add the broccoli.</p><p>Serve hot.</div></p></div>')
        found = directions(body, {'ingredients': []}, 'smitten-kitchen')
        self.assertEqual([s['text'] for s in found['steps']], ['Melt the butter.', 'Add the broccoli.', 'Serve hot.'])

    def test_jetpack_steps_outside_an_empty_directions_div(self):
        body = ('<div class="jetpack-recipe"><div class="jetpack-recipe-ingredients"><ul><li class="jetpack-recipe-ingredient">1 pound rigatoni</li></ul></div>'
            '<div class="jetpack-recipe-directions"></p><p></div><br/>Cook the pasta.</p><p>Do ahead: Sauce keeps 3 days.<br/></div></div>')
        found = directions(body, {'ingredients': ['1 pound rigatoni']}, 'smitten-kitchen')
        self.assertEqual([s['text'] for s in found['steps']], ['Cook the pasta.'])
        self.assertEqual(found['notes'], ['Do ahead: Sauce keeps 3 days.'])

    def test_old_post_keeps_titled_components_with_their_steps(self):
        body = ('<p>2 poblano chiles<br>1 head cauliflower<br>6 tortillas</p><p>Char the peppers.</p>'
            '<p><b>Cumin-Lime Crema</b></p><p>1/2 cup sour cream<br>1 tablespoon lime juice</p><p>Combine and serve.</p>'
            '<p>One year ago: Something else</p>')
        facts = parse_page('<h1>Quesadillas</h1><div class="entry-content">' + body + '</div>', 'https://smittenkitchen.com/2000/01/test/', 'smitten-kitchen')
        self.assertEqual(facts['ingredients'][-2:], ['1/2 cup sour cream', '1 tablespoon lime juice'])
        self.assertEqual(facts['ingredient_groups'], [{'at': 3, 'title': 'Cumin-Lime Crema'}])
        found = directions(body, facts, 'smitten-kitchen')
        self.assertEqual([(s['title'], s['text']) for s in found['steps']],
            [(None, 'Char the peppers.'), ('Cumin-Lime Crema', 'Combine and serve.')])

    def test_tasty_card_directions_notes_summary_and_groups(self):
        body = ('<div class="tasty-recipes"><div class="tasty-recipes-description"><p>Smoky rice.</p></div>'
            '<div class="tasty-recipes-ingredients-body"><p><strong>Rice</strong></p><ul><li>2 cups rice</li></ul>'
            '<p><strong>Topping</strong></p><ul><li>1 lemon</li><li>Parsley</li></ul></div>'
            '<div class="tasty-recipes-instructions-body"><ol><li>Toast the rice.</li><li>Bake.</li></ol></div>'
            '<div class="tasty-recipes-notes-body"><p>Make it ahead: chill overnight.</p></div></div>')
        found = directions(body, {'ingredients': ['2 cups rice', '1 lemon', 'Parsley']}, 'cookie-and-kate')
        self.assertEqual([s['text'] for s in found['steps']], ['Toast the rice.', 'Bake.'])
        self.assertEqual(found['notes'], ['Make it ahead: chill overnight.'])
        self.assertEqual(found['summary'], 'Smoky rice.')
        self.assertEqual(found['ingredient_groups'], [{'at': 0, 'title': 'Rice'}, {'at': 1, 'title': 'Topping'}])


class DietScreenTests(unittest.TestCase):
    def check(self, *ingredients, title='Test'):
        return screen({'_kind': 'blog', 'title': title, 'ingredients': list(ingredients)})[0]

    def test_plant_look_alikes_are_vegan(self):
        self.assertEqual(self.check('2 tablespoons peanut butter', '1 can coconut milk', '1 eggplant',
            '1 can butter beans', '¼ teaspoon cream of tartar', '1 cup vegan mozzarella', 'Swiss chard'), 'vegan')

    def test_required_dairy_egg_or_honey_is_not_vegan(self):
        for line in ('2 large eggs', '1 tablespoon honey', 'Grated Parmesan, for serving', '½ cup sour cream'):
            self.assertEqual(self.check('1 onion', line), 'not-vegan', line)

    def test_optional_or_alternative_animal_items_go_to_review(self):
        for line in ('Feta (optional)', '2 tablespoons butter or olive oil', '1 cup milk of choice',
                     '1 cup vegan mayo or regular mayonnaise'):
            self.assertEqual(self.check('1 onion', line), 'review', line)

    def test_a_recipe_titled_vegan_is_vegan(self):
        self.assertEqual(self.check('1 cup graham cracker crumbs', title='Vegan Lime Bars'), 'vegan')


if __name__ == '__main__': unittest.main()
